from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from dashboard_data import (
    load_overview_data,
    load_status_breakdown_data,
    load_ageing_buckets_data,
    load_trends_data,
)

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
def load_overview():
    return load_overview_data(DB_PATH)

@st.cache_data(show_spinner=False, ttl=10)
def load_status_breakdown():
    return load_status_breakdown_data(DB_PATH)

@st.cache_data(show_spinner=False, ttl=10)
def load_ageing_buckets():
    return load_ageing_buckets_data(DB_PATH)

@st.cache_data(show_spinner=False, ttl=30)
def load_trends():
    return load_trends_data(DB_PATH)


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
