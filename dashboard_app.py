from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime, timezone

import streamlit as st


DB_PATH = Path(__file__).resolve().parent / "inbox.db"
READY_STATUS = "VALID_PO"  # V1 truth: "ready" = VALID_PO


# ---------------- DB helpers ----------------
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> object:
    row = conn.execute(sql, params).fetchone()
    if not row:
        return None
    return list(row)[0]


def fetch_rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        scalar(
            conn,
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1;",
            (name,),
        )
        is not None
    )


# ---------------- Formatting helpers ----------------
def fmt_dt(value: object) -> str:
    if not value:
        return "—"
    s = str(value)
    return s.replace("T", " ").replace("+00:00", "")


def pence_or_zero(pence: object) -> int:
    try:
        return int(pence) if pence is not None else 0
    except (TypeError, ValueError):
        return 0


def pence_to_gbp_str(pence: object) -> str:
    # KPI cards should show £0.00 rather than —
    v = pence_or_zero(pence) / 100.0
    return f"£{v:,.2f}"


def pct_str(value: object) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "—"


def days_str(days: object) -> str:
    if days is None:
        return "—"
    try:
        return f"{int(days)} days"
    except (TypeError, ValueError):
        return "—"


# ---------------- Core metric engine ----------------
def overview_metrics() -> dict:
    """
    Returns the top-line metrics used by the Overview tab.
    """
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

        po_confidence = round((ready_count / total_present) * 100, 1) if total_present else None

        last_scan = scalar(conn, "SELECT MAX(last_scan_datetime) FROM inbox_invoice")

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
                # preserve timezone if present
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                oldest_days = (datetime.now(dt.tzinfo) - dt).days
            except Exception:
                oldest_days = None

        # Optional unread emails if inbox_message has is_read
        unread = None
        msg_cols = [r["name"] for r in conn.execute("PRAGMA table_info(inbox_message)").fetchall()]
        if "is_read" in msg_cols:
            unread = int(scalar(conn, "SELECT COUNT(*) FROM inbox_message WHERE is_read = 0") or 0)

        # Value coverage (present invoices with gross_total)
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
        value_coverage_pct = round((value_covered / total_present) * 100, 1) if total_present else None

        # Deterministic median workaround
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

        # NO_TEXT_LAYER count (OCR-required proxy)
        ocr_needed_count = int(
            scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM inbox_invoice
                WHERE is_currently_present = 1
                  AND po_match_status = 'NO_TEXT_LAYER'
                """,
            )
            or 0
        )

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
            "ocr_needed_count": ocr_needed_count,
        }


def status_breakdown_present() -> list[dict]:
    with get_connection() as conn:
        rows = fetch_rows(
            conn,
            """
            SELECT
                po_match_status AS status,
                COUNT(*) AS cnt,
                SUM(CASE WHEN gross_total IS NOT NULL AND gross_total > 0 THEN gross_total ELSE 0 END) AS gross_pence
            FROM inbox_invoice
            WHERE is_currently_present = 1
            GROUP BY po_match_status
            ORDER BY cnt DESC, status ASC
            """,
        )
        return [dict(r) for r in rows]


def ageing_buckets_present() -> list[dict]:
    """
    Age buckets (days since first_seen_datetime) split by Ready vs Manual.
    Uses DATE math inside SQLite (assumes ISO datetimes).
    """
    with get_connection() as conn:
        rows = fetch_rows(
            conn,
            f"""
            WITH base AS (
              SELECT
                document_hash,
                po_match_status,
                first_seen_datetime,
                CAST((julianday('now') - julianday(first_seen_datetime)) AS INTEGER) AS age_days,
                gross_total
              FROM inbox_invoice
              WHERE is_currently_present = 1
            ),
            bucketed AS (
              SELECT
                CASE
                  WHEN age_days <= 1 THEN '0-1 days'
                  WHEN age_days BETWEEN 2 AND 3 THEN '2-3 days'
                  WHEN age_days BETWEEN 4 AND 7 THEN '4-7 days'
                  WHEN age_days BETWEEN 8 AND 14 THEN '8-14 days'
                  ELSE '15+ days'
                END AS age_bucket,
                CASE WHEN po_match_status = ? THEN 'Ready' ELSE 'Manual' END AS lane,
                COUNT(*) AS cnt,
                SUM(CASE WHEN gross_total IS NOT NULL AND gross_total > 0 THEN gross_total ELSE 0 END) AS gross_pence
              FROM base
              GROUP BY age_bucket, lane
            )
            SELECT age_bucket, lane, cnt, gross_pence
            FROM bucketed
            ORDER BY
              CASE age_bucket
                WHEN '0-1 days' THEN 1
                WHEN '2-3 days' THEN 2
                WHEN '4-7 days' THEN 3
                WHEN '8-14 days' THEN 4
                ELSE 5
              END,
              lane DESC
            """,
            (READY_STATUS,),
        )
        return [dict(r) for r in rows]


def fetch_trends() -> list[dict]:
    """
    Reads from inbox_snapshot_daily if it exists.
    Schema is not enforced here; we just attempt a reasonable select.
    """
    with get_connection() as conn:
        if not table_exists(conn, "inbox_snapshot_daily"):
            return []

        # Expecting columns like:
        # snapshot_date, total_present, ready_count, manual_count, total_estimated_exposure_pence, po_confidence, value_coverage
        rows = fetch_rows(
            conn,
            """
            SELECT *
            FROM inbox_snapshot_daily
            ORDER BY snapshot_date DESC
            LIMIT 60
            """,
        )
        return [dict(r) for r in rows]


# ---------------- UI ----------------
st.set_page_config(page_title="AP Inbox Control", layout="wide")
st.title("AP Inbox Control")

m = overview_metrics()
st.caption(f"Most Recent Scan: {fmt_dt(m['last_scan'])}")

tabs = st.tabs(["Overview", "Exceptions", "Ageing", "Trends"])


# ---------------- Tab 1: Overview ----------------
with tabs[0]:
    c1, c2, c3, c4 = st.columns([1.4, 1.2, 1.2, 0.9])

    with c1:
        st.metric(
            "Total Estimated Exposure",
            pence_to_gbp_str(m["total_estimated_exposure_pence"]),
            f"{m['total_present']} invoices",
        )
        if m["missing_value_count"] > 0 and m["estimated_missing_exposure_pence"] is not None:
            st.caption(
                f"Value coverage: {m['value_covered']}/{m['total_present']} ({pct_str(m['value_coverage_pct'])}). "
                f"Includes estimate for {m['missing_value_count']} invoice(s) missing £ values "
                f"using median ({pence_to_gbp_str(m['median_gross_pence'])})."
            )
        else:
            st.caption(
                f"Value coverage: {m['value_covered']}/{m['total_present']} ({pct_str(m['value_coverage_pct'])}). "
                f"No estimation required."
            )

    with c2:
        st.metric(
            "Invoices awaiting manual review",
            pence_to_gbp_str(m["manual_exposure_pence"]),
            f"{m['manual_count']} invoices",
        )
        if m["missing_value_count"] > 0:
            st.caption("Note: some invoices may be missing £ values and are excluded from bucket totals until extracted.")

    with c3:
        st.metric(
            "Total invoices ready to be posted",
            pence_to_gbp_str(m["ready_exposure_pence"]),
            f"{m['ready_count']} invoices",
        )

    with c4:
        st.subheader("Signals")
        st.metric("PO confidence", pct_str(m["po_confidence"]))
        st.metric("Value coverage", pct_str(m["value_coverage_pct"]))
        st.metric("Biggest invoice", pence_to_gbp_str(m["biggest_invoice_pence"]))
        st.metric("Unread emails", str(m["unread"]) if m["unread"] is not None else "—")
        st.metric("Oldest invoice", days_str(m["oldest_days"]))

    st.divider()
    st.subheader("Total Estimated Exposure Over Time")
    st.caption("Use the Trends tab once snapshotting is enabled (V1.1).")


# ---------------- Tab 2: Exceptions ----------------
with tabs[1]:
    st.subheader("Exceptions & Status Breakdown")
    st.caption("Counts are for invoices currently present in the inbox. Values include only invoices where a gross total is available.")

    breakdown = status_breakdown_present()
    if not breakdown:
        st.info("No invoices currently present.")
    else:
        # High-level exception KPIs
        c1, c2, c3 = st.columns([1.1, 1.1, 1.1])
        with c1:
            st.metric("Unreadable (NO_TEXT_LAYER)", str(m["ocr_needed_count"]))
        with c2:
            st.metric("Ready invoices", str(m["ready_count"]))
        with c3:
            st.metric("Manual review invoices", str(m["manual_count"]))

        st.divider()

        # Breakdown table
        table = []
        for r in breakdown:
            table.append(
                {
                    "Status": r["status"],
                    "Count": int(r["cnt"]),
                    "Known £ total": pence_to_gbp_str(r["gross_pence"]),
                }
            )

        st.dataframe(table, use_container_width=True)


# ---------------- Tab 3: Ageing ----------------
with tabs[2]:
    st.subheader("Ageing Buckets")
    st.caption("Age is calculated from first seen datetime. Values include only invoices where a gross total is available.")

    rows = ageing_buckets_present()
    if not rows:
        st.info("No invoices currently present.")
    else:
        # Pivot-like display in a simple dataframe
        # We’ll output one row per bucket with Ready/Manual split.
        buckets = {}
        for r in rows:
            b = r["age_bucket"]
            lane = r["lane"]
            buckets.setdefault(b, {})
            buckets[b][lane] = {
                "cnt": int(r["cnt"]),
                "gross": pence_to_gbp_str(r["gross_pence"]),
            }

        out = []
        order = ["0-1 days", "2-3 days", "4-7 days", "8-14 days", "15+ days"]
        for b in order:
            ready = buckets.get(b, {}).get("Ready", {"cnt": 0, "gross": pence_to_gbp_str(0)})
            manual = buckets.get(b, {}).get("Manual", {"cnt": 0, "gross": pence_to_gbp_str(0)})
            out.append(
                {
                    "Age bucket": b,
                    "Ready count": ready["cnt"],
                    "Ready £": ready["gross"],
                    "Manual count": manual["cnt"],
                    "Manual £": manual["gross"],
                }
            )

        st.dataframe(out, use_container_width=True)

        st.divider()
        st.caption("Next upgrade (optional): add a small bar chart once the buckets have meaningful volume.")


# ---------------- Tab 4: Trends ----------------
with tabs[3]:
    st.subheader("Trends (Daily Snapshots)")
    st.caption(
        "This tab becomes active once daily snapshotting is enabled. "
        "Snapshots let you track exposure and workload trends over time."
    )

    trends = fetch_trends()
    if not trends:
        st.info(
            "Snapshotting not enabled yet. "
                )
    else:
        st.dataframe(trends, use_container_width=True)
        st.caption("Showing most recent snapshots (latest first).")
