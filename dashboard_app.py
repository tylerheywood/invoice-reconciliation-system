from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime

import streamlit as st


DB_PATH = Path(__file__).resolve().parent / "inbox.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> object:
    row = conn.execute(sql, params).fetchone()
    if not row:
        return None
    return list(row)[0]


def fmt_dt(value: object) -> str:
    if not value:
        return "—"
    s = str(value)
    return s.replace("T", " ").replace("+00:00", "")


def pence_to_gbp_str(pence: object) -> str:
    # For KPI cards, it's usually better to show £0.00 than "—"
    if pence is None:
        pence = 0
    try:
        v = int(pence) / 100.0
        return f"£{v:,.2f}"
    except (TypeError, ValueError):
        return "—"


def pence_or_zero(pence: object) -> int:
    try:
        return int(pence) if pence is not None else 0
    except (TypeError, ValueError):
        return 0


def overview_metrics() -> dict:
    READY_STATUS = "VALID_PO"  # V1 engine truth: ready invoices are VALID_PO

    with get_connection() as conn:
        total_present = int(
            scalar(conn, "SELECT COUNT(*) FROM inbox_invoice WHERE is_currently_present = 1") or 0
        )

        ready_count = int(
            scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM inbox_invoice
                WHERE is_currently_present = 1
                  AND po_match_status = ?
                """,
                (READY_STATUS,),
            )
            or 0
        )

        manual_count = int(
            scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM inbox_invoice
                WHERE is_currently_present = 1
                  AND po_match_status <> ?
                """,
                (READY_STATUS,),
            )
            or 0
        )

        # PO confidence % (how many present invoices are "ready")
        po_confidence = round((ready_count / total_present) * 100, 1) if total_present else None

        last_scan = scalar(conn, "SELECT MAX(last_scan_datetime) FROM inbox_invoice")

        # Oldest present invoice age (days)
        oldest_first_seen = scalar(
            conn,
            """
            SELECT MIN(first_seen_datetime)
            FROM inbox_invoice
            WHERE is_currently_present = 1
            """,
        )

        oldest_days = None
        if oldest_first_seen:
            try:
                dt = datetime.fromisoformat(str(oldest_first_seen).replace("Z", "+00:00"))
                oldest_days = (datetime.now(dt.tzinfo) - dt).days
            except Exception:
                oldest_days = None

        # Unread emails (optional)
        unread = None
        msg_cols = [r["name"] for r in conn.execute("PRAGMA table_info(inbox_message)").fetchall()]
        if "is_read" in msg_cols:
            unread = int(scalar(conn, "SELECT COUNT(*) FROM inbox_message WHERE is_read = 0") or 0)

        # ----- £ value coverage (pence) -----
        known_exposure_pence = scalar(
            conn,
            """
            SELECT SUM(gross_total)
            FROM inbox_invoice
            WHERE is_currently_present = 1
              AND gross_total IS NOT NULL
              AND gross_total > 0
            """,
        )

        biggest_invoice_pence = scalar(
            conn,
            """
            SELECT MAX(gross_total)
            FROM inbox_invoice
            WHERE is_currently_present = 1
              AND gross_total IS NOT NULL
              AND gross_total > 0
            """,
        )

        value_covered = int(
            scalar(
                conn,
                """
                SELECT SUM(CASE WHEN gross_total IS NOT NULL AND gross_total > 0 THEN 1 ELSE 0 END)
                FROM inbox_invoice
                WHERE is_currently_present = 1
                """,
            )
            or 0
        )
        missing_value_count = max(total_present - value_covered, 0)

        # Median gross_total (pence) for present invoices with values
        # (SQLite has no MEDIAN(); deterministic workaround.)
        median_gross_pence = scalar(
            conn,
            """
            SELECT AVG(gross_total) FROM (
                SELECT gross_total
                FROM inbox_invoice
                WHERE is_currently_present = 1
                  AND gross_total IS NOT NULL
                  AND gross_total > 0
                ORDER BY gross_total
                LIMIT 2 - (SELECT COUNT(*) FROM inbox_invoice
                           WHERE is_currently_present = 1
                             AND gross_total IS NOT NULL
                             AND gross_total > 0) % 2
                OFFSET (SELECT (COUNT(*) - 1) / 2 FROM inbox_invoice
                        WHERE is_currently_present = 1
                          AND gross_total IS NOT NULL
                          AND gross_total > 0)
            )
            """,
        )

        estimated_missing_exposure_pence = None
        if missing_value_count > 0 and median_gross_pence is not None:
            estimated_missing_exposure_pence = int(float(median_gross_pence)) * missing_value_count

        total_estimated_exposure_pence = pence_or_zero(known_exposure_pence) + pence_or_zero(
            estimated_missing_exposure_pence
        )

        # ----- Bucketed £ exposures (only where values exist) -----
        ready_exposure_pence = scalar(
            conn,
            """
            SELECT SUM(gross_total)
            FROM inbox_invoice
            WHERE is_currently_present = 1
              AND po_match_status = ?
              AND gross_total IS NOT NULL
              AND gross_total > 0
            """,
            (READY_STATUS,),
        )

        manual_exposure_pence = scalar(
            conn,
            """
            SELECT SUM(gross_total)
            FROM inbox_invoice
            WHERE is_currently_present = 1
              AND po_match_status <> ?
              AND gross_total IS NOT NULL
              AND gross_total > 0
            """,
            (READY_STATUS,),
        )

        # Value coverage % (distinct from PO confidence)
        value_coverage_pct = round((value_covered / total_present) * 100, 1) if total_present else None

        return {
            "total_present": total_present,
            "ready_count": ready_count,
            "manual_count": manual_count,
            "po_confidence": po_confidence,
            "value_coverage_pct": value_coverage_pct,
            "last_scan": last_scan,
            "oldest_days": oldest_days,
            "unread": unread,
            "known_exposure_pence": known_exposure_pence,
            "estimated_missing_exposure_pence": estimated_missing_exposure_pence,
            "total_estimated_exposure_pence": total_estimated_exposure_pence,
            "median_gross_pence": median_gross_pence,
            "biggest_invoice_pence": biggest_invoice_pence,
            "value_covered": value_covered,
            "missing_value_count": missing_value_count,
            "ready_exposure_pence": ready_exposure_pence,
            "manual_exposure_pence": manual_exposure_pence,
        }


# ---------------- UI ----------------
st.set_page_config(page_title="AP Inbox Control", layout="wide")
st.title("AP Inbox Control")

m = overview_metrics()

st.caption(f"Most Recent Scan: {fmt_dt(m['last_scan'])}")

tabs = st.tabs(["Overview"])

with tabs[0]:
    c1, c2, c3, c4 = st.columns([1.4, 1.2, 1.2, 0.9])

    # ----- Total Estimated Exposure -----
    with c1:
        st.metric(
            "Total Estimated Exposure",
            pence_to_gbp_str(m["total_estimated_exposure_pence"]),
            f"{m['total_present']} invoices",
        )

        # Explicit disclosure: estimate applies only to invoices missing £ values
        if m["missing_value_count"] > 0 and m["estimated_missing_exposure_pence"] is not None:
            st.caption(
                f"Value coverage: {m['value_covered']}/{m['total_present']} ({m['value_coverage_pct']}%). "
                f"Includes estimate for {m['missing_value_count']} invoice(s) missing £ values "
                f"using median ({pence_to_gbp_str(m['median_gross_pence'])})."
            )
        else:
            st.caption(
                f"Value coverage: {m['value_covered']}/{m['total_present']} ({m['value_coverage_pct']}%). "
                f"No estimation required."
            )

    # ----- Manual Review -----
    with c2:
        st.metric(
            "Invoices awaiting manual review",
            pence_to_gbp_str(m["manual_exposure_pence"]),
            f"{m['manual_count']} invoices",
        )
        if m["missing_value_count"] > 0:
            st.caption("Note: some invoices may be missing £ values and are excluded from bucket totals until extracted.")

    # ----- Ready to post -----
    with c3:
        st.metric(
            "Total invoices ready to be posted",
            pence_to_gbp_str(m["ready_exposure_pence"]),
            f"{m['ready_count']} invoices",
        )

    # ----- Signals -----
    with c4:
        st.subheader("Signals")
        st.metric("PO confidence", f"{m['po_confidence']}%" if m["po_confidence"] is not None else "—")
        st.metric("Value coverage", f"{m['value_coverage_pct']}%" if m["value_coverage_pct"] is not None else "—")
        st.metric("Biggest invoice", pence_to_gbp_str(m["biggest_invoice_pence"]))
        st.metric("Unread emails", str(m["unread"]) if m["unread"] is not None else "—")
        st.metric("Oldest invoice", f"{m['oldest_days']} days" if m["oldest_days"] is not None else "—")

    st.divider()
    st.subheader("Total Estimated Exposure Over Time")
    st.caption("To be confirmed — we’ll add snapshotting once V1 metrics are stable.")
