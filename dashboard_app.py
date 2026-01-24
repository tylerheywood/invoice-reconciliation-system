from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st

# =============================================================================
# AP Inbox Control — Dashboard (V1)
#
# Ledger contract (dashboard expectations):
# - Table: inbox_invoice
#   - is_currently_present (int 0/1)
#   - first_seen_datetime (ISO string)
#   - last_scan_datetime (ISO string)
#   - gross_total (pence int, nullable)
#
# Readiness contract (preferred order):
#  1) If column `ready_to_post` exists (0/1) => dashboard uses it.
#  2) Else dashboard falls back to po_match_status in READY_STATUSES.
#


# - Table: inbox_snapshot_daily (for trends)
# =============================================================================

APP_TITLE = "AP Inbox Control"
DB_PATH = Path(__file__).resolve().parent / "inbox.db"

# Fallback readiness values if `ready_to_post` is not present.
READY_STATUSES = ("VALID_PO",)  # add more values as needed


# -------------------- DB + schema helpers --------------------
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
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


def get_table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


@dataclass(frozen=True)
class ReadinessRule:
    ready_predicate_sql: str
    ready_params: tuple
    manual_predicate_sql: str
    manual_params: tuple
    source: str


def build_readiness_rule(invoice_cols: set[str]) -> ReadinessRule:
    """
    Returns the readiness rule the dashboard will use.
    Priority:
      1) ready_to_post column (0/1)
      2) po_match_status in READY_STATUSES
      3) fallback: nothing is ready (safe failure)
    """
    if "ready_to_post" in invoice_cols:
        return ReadinessRule(
            ready_predicate_sql="ready_to_post = 1",
            ready_params=(),
            manual_predicate_sql="(ready_to_post IS NULL OR ready_to_post <> 1)",
            manual_params=(),
            source="ready_to_post",
        )

    if "po_match_status" in invoice_cols:
        placeholders = ", ".join(["?"] * len(READY_STATUSES))
        return ReadinessRule(
            ready_predicate_sql=f"po_match_status IN ({placeholders})",
            ready_params=tuple(READY_STATUSES),
            manual_predicate_sql=f"(po_match_status IS NULL OR po_match_status NOT IN ({placeholders}))",
            manual_params=tuple(READY_STATUSES),
            source="po_match_status",
        )

    return ReadinessRule(
        ready_predicate_sql="0 = 1",
        ready_params=(),
        manual_predicate_sql="1 = 1",
        manual_params=(),
        source="fallback_none_ready",
    )


# -------------------- Formatting helpers --------------------
def fmt_dt(value: Any) -> str:
    if not value:
        return "—"
    s = str(value)
    return s.replace("T", " ").replace("+00:00", "").replace("Z", "")


def pence_or_zero(pence: Any) -> int:
    try:
        return int(pence) if pence is not None else 0
    except (TypeError, ValueError):
        return 0


def pence_to_gbp_str(pence: Any) -> str:
    v = pence_or_zero(pence) / 100.0
    return f"£{v:,.2f}"


def pct_str(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "—"


def days_str(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(value)} days"
    except (TypeError, ValueError):
        return "—"


def parse_iso_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# -------------------- Cached data access --------------------
@st.cache_data(show_spinner=False, ttl=10)
def load_overview() -> dict[str, Any]:
    with get_connection() as conn:
        if not table_exists(conn, "inbox_invoice"):
            return {"_error": "Missing table: inbox_invoice"}

        invoice_cols = get_table_columns(conn, "inbox_invoice")
        rule = build_readiness_rule(invoice_cols)

        total_present = int(
            scalar(conn, "SELECT COUNT(*) FROM inbox_invoice WHERE is_currently_present = 1") or 0
        )

        ready_count = int(
            scalar(
                conn,
                f"""
                SELECT COUNT(*)
                FROM inbox_invoice
                WHERE is_currently_present = 1
                  AND ({rule.ready_predicate_sql})
                """,
                rule.ready_params,
            )
            or 0
        )

        manual_count = int(
            scalar(
                conn,
                f"""
                SELECT COUNT(*)
                FROM inbox_invoice
                WHERE is_currently_present = 1
                  AND ({rule.manual_predicate_sql})
                """,
                rule.manual_params,
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
        dt_oldest = parse_iso_dt(oldest_first_seen)
        if dt_oldest:
            oldest_days = (datetime.now(dt_oldest.tzinfo) - dt_oldest).days



        # ---------------- Overall exposure (known + estimated) ----------------
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

        # Deterministic median (pence)
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

        # ---------------- Ready lane exposure (known only) ----------------
        ready_exposure_pence = scalar(
            conn,
            f"""
            SELECT SUM(gross_total)
            FROM inbox_invoice
            WHERE is_currently_present = 1
              AND ({rule.ready_predicate_sql})
              AND gross_total IS NOT NULL
              AND gross_total > 0
            """,
            rule.ready_params,
        )
        ready_known_exposure_pence = ready_exposure_pence  # explicit for UI clarity

        # ---------------- Manual lane exposure (known + estimated) ----------------
        manual_known_exposure_pence = scalar(
            conn,
            f"""
            SELECT SUM(gross_total)
            FROM inbox_invoice
            WHERE is_currently_present = 1
              AND ({rule.manual_predicate_sql})
              AND gross_total IS NOT NULL
              AND gross_total > 0
            """,
            rule.manual_params,
        )

        manual_value_covered = int(
            scalar(
                conn,
                f"""
                SELECT SUM(CASE WHEN gross_total IS NOT NULL AND gross_total > 0 THEN 1 ELSE 0 END)
                FROM inbox_invoice
                WHERE is_currently_present = 1
                  AND ({rule.manual_predicate_sql})
                """,
                rule.manual_params,
            )
            or 0
        )
        manual_missing_value_count = max(manual_count - manual_value_covered, 0)

        manual_estimated_missing_exposure_pence = None
        if manual_missing_value_count > 0 and median_gross_pence is not None:
            manual_estimated_missing_exposure_pence = int(float(median_gross_pence)) * manual_missing_value_count

        manual_total_estimated_exposure_pence = pence_or_zero(manual_known_exposure_pence) + pence_or_zero(
            manual_estimated_missing_exposure_pence
        )

        # NO_TEXT_LAYER count (only if po_match_status exists)
        ocr_needed_count = None
        if "po_match_status" in invoice_cols:
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
            "readiness_source": rule.source,
            "total_present": total_present,
            "ready_count": ready_count,
            "manual_count": manual_count,
            "po_confidence": po_confidence,
            "value_coverage_pct": value_coverage_pct,
            "last_scan": last_scan,
            "oldest_days": oldest_days,
            # totals (overall)
            "known_exposure_pence": known_exposure_pence,
            "estimated_missing_exposure_pence": estimated_missing_exposure_pence,
            "total_estimated_exposure_pence": total_estimated_exposure_pence,
            "median_gross_pence": median_gross_pence,
            "biggest_invoice_pence": biggest_invoice_pence,
            "value_covered": value_covered,
            "missing_value_count": missing_value_count,
            # ready lane
            "ready_exposure_pence": ready_exposure_pence,
            "ready_known_exposure_pence": ready_known_exposure_pence,
            # manual lane
            "manual_known_exposure_pence": manual_known_exposure_pence,
            "manual_estimated_missing_exposure_pence": manual_estimated_missing_exposure_pence,
            "manual_total_estimated_exposure_pence": manual_total_estimated_exposure_pence,
            "manual_missing_value_count": manual_missing_value_count,
            # other
            "ocr_needed_count": ocr_needed_count,
        }


@st.cache_data(show_spinner=False, ttl=10)
def load_status_breakdown() -> list[dict[str, Any]]:
    with get_connection() as conn:
        if not table_exists(conn, "inbox_invoice"):
            return []
        cols = get_table_columns(conn, "inbox_invoice")
        if "po_match_status" not in cols:
            return []

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


@st.cache_data(show_spinner=False, ttl=10)
def load_ageing_buckets() -> list[dict[str, Any]]:
    with get_connection() as conn:
        if not table_exists(conn, "inbox_invoice"):
            return []
        cols = get_table_columns(conn, "inbox_invoice")
        rule = build_readiness_rule(cols)

        rows = fetch_rows(
            conn,
            f"""
            WITH base AS (
              SELECT
                first_seen_datetime,
                gross_total,
                CASE WHEN ({rule.ready_predicate_sql}) THEN 1 ELSE 0 END AS is_ready,
                CAST((julianday('now') - julianday(first_seen_datetime)) AS INTEGER) AS age_days
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
                CASE WHEN is_ready = 1 THEN 'Ready' ELSE 'Manual' END AS lane,
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
            rule.ready_params,
        )
        return [dict(r) for r in rows]


@st.cache_data(show_spinner=False, ttl=30)
def load_trends() -> list[dict[str, Any]]:
    with get_connection() as conn:
        if not table_exists(conn, "inbox_snapshot_daily"):
            return []
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


# -------------------- UI --------------------
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

m = load_overview()
if "_error" in m:
    st.error(m["_error"])
    st.stop()

st.caption(f"Most Recent Scan: {fmt_dt(m['last_scan'])}")
st.caption("Disclosure: Exposure values may include a median-based estimate where invoices are missing extracted £ totals.")

tabs = st.tabs(["Overview", "Exceptions", "Ageing", "Trends"])

# ---------------- Tab 1: Overview ----------------
with tabs[0]:
    show_breakdown = st.toggle("Show exposure breakdown (Known vs Estimated)", value=False)

    c1, c2, c3, c4 = st.columns([1.4, 1.2, 1.2, 0.9])

    with c1:
        st.metric(
            "Total Estimated Exposure",
            pence_to_gbp_str(m["total_estimated_exposure_pence"]),
            f"{m['total_present']} invoices",
        )

        # Your existing coverage caption (kept)
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

        # Option A: always-visible Known vs Estimated
        st.caption(
            f"Known: {pence_to_gbp_str(m['known_exposure_pence'])} · "
            f"Estimated: {pence_to_gbp_str(m['estimated_missing_exposure_pence'])}"
        )

        # Option B: extra detail on toggle
        if show_breakdown:
            st.caption("Estimate method: median of invoices with extracted £ totals × count of missing values.")

    with c2:
        st.metric(
            "Invoices awaiting manual review",
            pence_to_gbp_str(m["manual_total_estimated_exposure_pence"]),
            f"{m['manual_count']} invoices",
        )

        # Option A: always-visible Known vs Estimated for manual lane
        st.caption(
            f"Known: {pence_to_gbp_str(m['manual_known_exposure_pence'])} · "
            f"Estimated: {pence_to_gbp_str(m['manual_estimated_missing_exposure_pence'])}"
        )

        # Option B: extra detail on toggle (only when it matters)
        if show_breakdown and m.get("manual_missing_value_count", 0) > 0 and m.get("median_gross_pence") is not None:
            st.caption(
                f"Manual lane estimate covers {m['manual_missing_value_count']} invoice(s) missing £ values "
                f"using median ({pence_to_gbp_str(m['median_gross_pence'])})."
            )

    with c3:
        st.metric(
            "Invoices ready to be posted",
            pence_to_gbp_str(m["ready_exposure_pence"]),
            f"{m['ready_count']} invoices",
        )

        # Option A: always-visible Known vs Estimated for ready lane (no estimate)
        st.caption(f"Known: {pence_to_gbp_str(m['ready_known_exposure_pence'])} · Estimated: £0.00")

        # Option B: extra detail on toggle
        if show_breakdown:
            st.caption("Ready lane uses extracted totals only (no estimation).")

    with c4:
        st.subheader("Signals")
        st.metric("PO confidence", pct_str(m["po_confidence"]))
        st.metric("Biggest invoice", pence_to_gbp_str(m["biggest_invoice_pence"]))
        st.metric("Oldest invoice", days_str(m["oldest_days"]))

    st.divider()
    st.subheader("Total Estimated Exposure Over Time")
    st.caption("Use the Trends tab once snapshotting is enabled (V1.1).")

# ---------------- Tab 2: Exceptions ----------------
with tabs[1]:
    st.subheader("Exceptions & Status Breakdown")
    st.caption(
        "Counts are for invoices currently present in the inbox. "
        "Values include only invoices where a gross total is available."
    )

    breakdown = load_status_breakdown()
    if not breakdown:
        st.info("No status breakdown available (missing `po_match_status` or no invoices present).")
    else:
        c1, c2, c3 = st.columns([1.1, 1.1, 1.1])
        with c1:
            st.metric(
                "Unreadable (NO_TEXT_LAYER)",
                str(m["ocr_needed_count"]) if m["ocr_needed_count"] is not None else "—",
            )
        with c2:
            st.metric("Manual review invoices", str(m["manual_count"]))
        with c3:
            st.metric("Ready invoices", str(m["ready_count"]))

        st.divider()

        table = [
            {
                "Status": r["status"],
                "Count": int(r["cnt"]),
                "Known total (£)": pence_to_gbp_str(r["gross_pence"]),
            }
            for r in breakdown
        ]
        st.dataframe(table, use_container_width=True)

# ---------------- Tab 3: Ageing ----------------
with tabs[2]:
    st.subheader("Ageing Buckets")
    st.caption("Age is calculated from first seen datetime. Values include only invoices where a gross total is available.")

    rows = load_ageing_buckets()
    if not rows:
        st.info("No invoices currently present.")
    else:
        buckets: dict[str, dict[str, dict[str, Any]]] = {}
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

# ---------------- Tab 4: Trends ----------------
with tabs[3]:
    st.subheader("Trends (Daily Snapshots)")
    st.caption(
        "This tab becomes active once daily snapshotting is enabled. "
        "Snapshots let you track exposure and workload trends over time."
    )

    trends = load_trends()
    if not trends:
        st.info("Snapshotting not enabled yet.")
    else:
        st.dataframe(trends, use_container_width=True)
        st.caption("Showing most recent snapshots (latest first).")
