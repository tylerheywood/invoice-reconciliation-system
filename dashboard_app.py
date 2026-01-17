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
    # your values look ISO-ish already
    return s.replace("T", " ").replace("+00:00", "")


def overview_metrics() -> dict:
    with get_connection() as conn:
        total_present = int(
            scalar(
                conn,
                "SELECT COUNT(*) FROM inbox_invoice WHERE is_currently_present = 1",
            )
            or 0
        )

        ready = int(
            scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM inbox_invoice
                WHERE is_currently_present = 1
                  AND po_match_status = 'SINGLE_PO_DETECTED'
                """,
            )
            or 0
        )

        manual = total_present - ready

        # confidence %
        confidence = round((ready / total_present) * 100, 1) if total_present else None

        # newest scan timestamp
        last_scan = scalar(
            conn,
            "SELECT MAX(last_scan_datetime) FROM inbox_invoice",
        )

        # oldest present invoice age in days (based on first_seen_datetime)
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
                # robust enough for your current ISO strings
                dt = datetime.fromisoformat(str(oldest_first_seen).replace("Z", "+00:00"))
                oldest_days = (datetime.now(dt.tzinfo) - dt).days
            except Exception:
                oldest_days = None

        # unread emails (only if you store it on inbox_message)
        # If you don't have is_read yet, leave as None.
        unread = None
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(inbox_message)").fetchall()]
        if "is_read" in cols:
            unread = int(
                scalar(
                    conn,
                    "SELECT COUNT(*) FROM inbox_message WHERE is_read = 0",
                )
                or 0
            )

        return {
            "total_present": total_present,
            "ready": ready,
            "manual": manual,
            "confidence": confidence,
            "last_scan": last_scan,
            "oldest_days": oldest_days,
            "unread": unread,
        }


# ---------------- UI ----------------
st.set_page_config(page_title="AP Inbox Control", layout="wide")
st.title("AP Inbox Control")

m = overview_metrics()

# Top bar
st.caption(f"Most Recent Scan: {fmt_dt(m['last_scan'])}")

tabs = st.tabs(["Overview"])

with tabs[0]:
    # Main KPIs across
    c1, c2, c3, c4 = st.columns([1.4, 1.2, 1.2, 0.9])

    with c1:
        st.metric("Total Estimated Exposure", "—", f"{m['total_present']} invoices")
        st.caption("£ values coming once gross_total is populated.")

    with c2:
        st.metric("Invoices awaiting manual review", "—", f"{m['manual']} invoices")

    with c3:
        st.metric("Total invoices ready to be posted", "—", f"{m['ready']} invoices")

    with c4:
        st.subheader("Signals")
        st.metric("Confidence", f"{m['confidence']}%" if m["confidence"] is not None else "—")
        st.metric("Biggest invoice", "—")
        st.metric("Unread emails", str(m["unread"]) if m["unread"] is not None else "—")
        st.metric("Oldest invoice", f"{m['oldest_days']} days" if m["oldest_days"] is not None else "—")

    st.divider()
    st.subheader("Total Estimated Exposure Over Time")
    st.caption("To be confirmed — we’ll add snapshotting once V1 metrics are stable.")
