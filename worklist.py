# worklist.py
"""
V1 Worklist (Joblist) for ICS

- Deterministic, auditable, precedence-based "next action" per invoice
- Produces two key outputs:
    - next_action
    - action_reason
- Writes:
    - invoice_worklist (current cache, full-replace per run)
    - invoice_worklist_history (append-only snapshots per run)

This is the V1 "B" model:
- No manual removal / dismissal state
- Items disappear when underlying truth changes or invoice is no longer present

Debug:
- Controlled via ICS_DEBUG env var
- Prints summary metric for action changes vs previous run (history table)
"""

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
import uuid
from typing import Tuple, List, Dict, Any

from po_validation import (
    STATUS_UNVALIDATED,
    STATUS_PO_NOT_IN_MASTER,
    STATUS_PO_NOT_OPEN,
    STATUS_VALID_PO,
    STATUS_SINGLE_PO_DETECTED,
)

# Debug toggle (matches your project pattern)
_ENV_DEBUG = os.getenv("ICS_DEBUG", "").strip().lower()
DEBUG = _ENV_DEBUG in ("1", "true", "yes", "y", "on")

# If you later add constants in po_detection.py, import them here and stop using raw strings.
STATUS_NO_TEXT_LAYER = "NO_TEXT_LAYER"
STATUS_MISSING_PO = "MISSING_PO"
STATUS_MULTIPLE_POS = "MULTIPLE_POS"


@dataclass(frozen=True)
class WorkItem:
    document_hash: str
    next_action: str
    action_reason: str
    priority: int
    generated_at_utc: str
    is_currently_present: int


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_run_id() -> str:
    return uuid.uuid4().hex


def build_worklist(
    conn: sqlite3.Connection,
    *,
    only_currently_present: bool = True,
    include_ready_to_post: bool = True,
) -> Tuple[str, List[WorkItem]]:
    """
    Compute the current worklist deterministically from inbox_invoice truth columns.

    Returns:
        (run_id, items)

    Notes:
    - Does NOT write to DB.
    - Precedence based: first blocker wins.
    """
    generated_at_utc = _utc_now_iso()
    run_id = _new_run_id()

    where_clause = "WHERE ii.is_currently_present = 1" if only_currently_present else ""

    rows = conn.execute(
        f"""
        SELECT
            ii.document_hash,
            ii.is_currently_present,
            ii.po_match_status,
            ii.po_validation_status,
            ii.ready_to_post,
            ii.net_total,
            ii.vat_total,
            ii.gross_total
        FROM inbox_invoice ii
        {where_clause}
        """
    ).fetchall()

    items: List[WorkItem] = []
    for r in rows:
        next_action, action_reason, priority = _classify_invoice(r)

        if not include_ready_to_post and next_action == "READY TO POST":
            continue

        items.append(
            WorkItem(
                document_hash=r["document_hash"],
                next_action=next_action,
                action_reason=action_reason,
                priority=priority,
                generated_at_utc=generated_at_utc,
                is_currently_present=int(r["is_currently_present"]),
            )
        )

    # Stable ordering (deterministic)
    items.sort(key=lambda x: (x.priority, x.document_hash))
    return run_id, items


def refresh_worklist_tables(
    conn: sqlite3.Connection,
    *,
    only_currently_present: bool = True,
    include_ready_to_post: bool = True,
) -> str:
    """
    Full-replace refresh of invoice_worklist + append-only snapshot to history.

    Returns:
        run_id for this refresh.
    """
    run_id, items = build_worklist(
        conn,
        only_currently_present=only_currently_present,
        include_ready_to_post=include_ready_to_post,
    )

    # One transaction: either it all lands or nothing does.
    with conn:
        conn.execute("DELETE FROM invoice_worklist;")

        conn.executemany(
            """
            INSERT INTO invoice_worklist (
                document_hash,
                next_action,
                action_reason,
                priority,
                generated_at_utc,
                is_currently_present
            )
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            [
                (
                    i.document_hash,
                    i.next_action,
                    i.action_reason,
                    i.priority,
                    i.generated_at_utc,
                    i.is_currently_present,
                )
                for i in items
            ],
        )

        conn.executemany(
            """
            INSERT INTO invoice_worklist_history (
                run_id,
                document_hash,
                next_action,
                action_reason,
                priority,
                generated_at_utc,
                is_currently_present
            )
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            [
                (
                    run_id,
                    i.document_hash,
                    i.next_action,
                    i.action_reason,
                    i.priority,
                    i.generated_at_utc,
                    i.is_currently_present,
                )
                for i in items
            ],
        )

    if DEBUG:
        _debug_worklist_delta(conn, run_id, total_items=len(items))

    return run_id


def fetch_current_worklist(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    Convenience reader (dashboard/CLI): returns dict rows from invoice_worklist,
    ordered by priority then document_hash.
    """
    rows = conn.execute(
        """
        SELECT
            document_hash,
            next_action,
            action_reason,
            priority,
            generated_at_utc,
            is_currently_present
        FROM invoice_worklist
        ORDER BY priority ASC, document_hash ASC;
        """
    ).fetchall()
    return [dict(r) for r in rows]


# ----------------------------
# Debug helpers
# ----------------------------

def _debug_worklist_delta(conn: sqlite3.Connection, run_id: str, *, total_items: int) -> None:
    """
    Debug-only: compares this run against the previous run and prints action-change counts.
    Read-only. No mutations.
    """
    # Find previous run_id (excluding current)
    prev = conn.execute(
        """
        SELECT run_id
        FROM invoice_worklist_history
        WHERE run_id <> ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (run_id,),
    ).fetchone()

    if prev is None:
        print("\n[WORKLIST DEBUG]")
        print(f"Run ID: {run_id}")
        print(f"Total items this run: {total_items}")
        print("First run — no prior worklist to compare.")
        return

    prev_run_id = prev["run_id"]

    changes = conn.execute(
        """
        SELECT
            prev.next_action AS prev_action,
            curr.next_action AS curr_action,
            COUNT(*) AS count
        FROM invoice_worklist_history prev
        JOIN invoice_worklist_history curr
          ON prev.document_hash = curr.document_hash
        WHERE prev.run_id = ?
          AND curr.run_id = ?
          AND prev.next_action <> curr.next_action
        GROUP BY prev.next_action, curr.next_action
        ORDER BY count DESC;
        """,
        (prev_run_id, run_id),
    ).fetchall()

    total_changed = sum(r["count"] for r in changes)

    print("\n[WORKLIST DEBUG]")
    print(f"Run ID: {run_id}")
    print(f"Total items this run: {total_items}")
    print(f"Changed since last run: {total_changed}")

    if total_changed == 0:
        print("  No action changes detected.")
        return

    for r in changes:
        print(f"  {r['prev_action']} → {r['curr_action']}: {r['count']}")


# ----------------------------
# Classification rules (V1)
# ----------------------------

def _values_missing(row: sqlite3.Row) -> bool:
    """
    V1: allow 'gross-only' invoices (common for international vendors).
    Requirement for readiness/worklist is gross_total presence.
    Net/VAT may be NULL and that's acceptable in V1.
    """
    return row["gross_total"] is None


def _classify_invoice(row: sqlite3.Row) -> Tuple[str, str, int]:
    """
    Return (next_action, action_reason, priority).

    Priority: lower = earlier in the queue.

    Precedence order (V1):
    1) Not present (if included)
    2) No text layer
    3) Missing PO
    4) Multiple POs
    5) Single PO + validation blocks
    6) Ready to post
    7) Gross total missing
    8) Catch-all manual review
    """
    if int(row["is_currently_present"]) == 0:
        return ("NOT CURRENTLY PRESENT", "NOT IN INBOX THIS SCAN", 90)

    po_match_status = (row["po_match_status"] or "").strip()
    po_validation_status = (row["po_validation_status"] or "").strip()
    ready_to_post = int(row["ready_to_post"]) if row["ready_to_post"] is not None else 0

    # 1) No text layer (scanned/blocked)
    if po_match_status == STATUS_NO_TEXT_LAYER:
        return ("MANUAL REVIEW", "NO TEXT LAYER", 10)

    # 2) PO missing
    if po_match_status == STATUS_MISSING_PO:
        return ("REQUEST PO", "MISSING PO", 20)

    # 3) Multiple POs
    if po_match_status == STATUS_MULTIPLE_POS:
        return ("SELECT CORRECT PO", "MULTIPLE POS DETECTED", 30)

    # 4) Single PO detected but validation blocks posting
    if po_match_status == STATUS_SINGLE_PO_DETECTED:
        if po_validation_status == STATUS_PO_NOT_IN_MASTER:
            return ("CHECK PO MASTER", "PO NOT IN MASTER", 40)
        if po_validation_status == STATUS_PO_NOT_OPEN:
            return ("CHECK PO STATUS", "PO NOT OPEN", 50)
        if po_validation_status == STATUS_UNVALIDATED:
            return ("MANUAL REVIEW", "PO NOT VALIDATED YET", 60)

        # Defensive: unknown status written somewhere
        if po_validation_status not in (
            STATUS_UNVALIDATED,
            STATUS_PO_NOT_IN_MASTER,
            STATUS_PO_NOT_OPEN,
            STATUS_VALID_PO,
        ):
            return ("MANUAL REVIEW", "UNKNOWN PO VALIDATION STATUS", 65)

    # 5) Ready to post (canonical green lane)
    if ready_to_post == 1:
        return ("READY TO POST", "VALID PO", 80)

    # 6) Gross missing => needs human value entry/confirmation
    if _values_missing(row):
        return ("ENTER / CONFIRM VALUE", "GROSS TOTAL NOT EXTRACTED", 70)

    # 7) Catch-all
    return ("MANUAL REVIEW", "UNCLASSIFIED STATE", 85)
