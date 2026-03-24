"""
Worklist engine for the IRS pipeline.

Computes a deterministic, precedence-based "next action" per invoice and writes
the results to invoice_worklist (current cache) and invoice_worklist_history
(append-only audit trail).
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Tuple, List, Dict, Any

from .po_validation import (
    STATUS_UNVALIDATED,
    STATUS_PO_NOT_IN_MASTER,
    STATUS_PO_NOT_OPEN,
    STATUS_VALID_PO,
    STATUS_SINGLE_PO_DETECTED,
    STATUS_PO_NOT_CONFIRMED,
)

_ENV_DEBUG = os.getenv("ICS_DEBUG", "").strip().lower()
DEBUG = _ENV_DEBUG in ("1", "true", "yes", "y", "on")

STATUS_NO_TEXT_LAYER = "NO_TEXT_LAYER"
STATUS_MISSING_PO = "MISSING_PO"
STATUS_MULTIPLE_POS = "MULTIPLE_POS"


@dataclass(frozen=True)
class WorkItem:
    document_hash: str
    file_name: str | None
    scanned_datetime: str | None
    review_note: str | None
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
    """Compute the current worklist from invoice truth columns. Returns (run_id, items)."""
    generated_at_utc = _utc_now_iso()
    run_id = _new_run_id()

    where_clause = "WHERE id.is_currently_present = 1" if only_currently_present else ""

    rows = conn.execute(
        f"""
        SELECT
            id.document_hash,
            id.is_currently_present,
            id.po_match_status,
            id.po_validation_status,
            id.ready_to_post,
            id.net_total,
            id.vat_total,
            id.gross_total,
            id.file_name        AS file_name,
            id.review_note,
            id.posted_datetime,
            COALESCE(id.duplicate_suspect, 0) AS duplicate_suspect,
            if2.scanned_datetime AS scanned_datetime
        FROM invoice_document id
        LEFT JOIN invoice_file if2 ON if2.file_id = id.file_id
        {where_clause}
        """
    ).fetchall()

    items: List[WorkItem] = []
    for r in rows:
        # Posted invoices are terminal — exclude from worklist
        if r["posted_datetime"] is not None:
            continue

        next_action, action_reason, priority = _classify_invoice(r)

        if not include_ready_to_post and next_action == "READY TO POST":
            continue

        items.append(
            WorkItem(
                document_hash=r["document_hash"],
                file_name=r["file_name"],
                scanned_datetime=r["scanned_datetime"],
                review_note=r["review_note"],
                next_action=next_action,
                action_reason=action_reason,
                priority=priority,
                generated_at_utc=generated_at_utc,
                is_currently_present=int(r["is_currently_present"]),
            )
        )

    items.sort(key=lambda x: (x.priority, x.document_hash))
    return run_id, items


def refresh_worklist_tables(
    conn: sqlite3.Connection,
    *,
    only_currently_present: bool = True,
    include_ready_to_post: bool = True,
) -> str:
    """Full-replace refresh of invoice_worklist + append snapshot to history. Returns run_id."""
    run_id, items = build_worklist(
        conn,
        only_currently_present=only_currently_present,
        include_ready_to_post=include_ready_to_post,
    )

    with conn:
        conn.execute("DELETE FROM invoice_worklist;")

        conn.executemany(
            """
            INSERT INTO invoice_worklist (
                document_hash, file_name, scanned_datetime, review_note,
                next_action, action_reason, priority,
                generated_at_utc, is_currently_present
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            [(i.document_hash, i.file_name, i.scanned_datetime, i.review_note,
              i.next_action, i.action_reason, i.priority,
              i.generated_at_utc, i.is_currently_present) for i in items],
        )

        conn.executemany(
            """
            INSERT INTO invoice_worklist_history (
                run_id, document_hash, file_name, scanned_datetime, review_note,
                next_action, action_reason, priority,
                generated_at_utc, is_currently_present
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            [(run_id, i.document_hash, i.file_name, i.scanned_datetime, i.review_note,
              i.next_action, i.action_reason, i.priority,
              i.generated_at_utc, i.is_currently_present) for i in items],
        )

    if DEBUG:
        _debug_worklist_delta(conn, run_id, total_items=len(items))

    return run_id


def fetch_current_worklist(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Return current worklist rows as dicts."""
    rows = conn.execute("""
        SELECT document_hash, file_name, scanned_datetime, review_note,
               next_action, action_reason, priority,
               generated_at_utc, is_currently_present
        FROM invoice_worklist
        ORDER BY priority ASC, document_hash ASC;
    """).fetchall()
    return [dict(r) for r in rows]


def _debug_worklist_delta(conn: sqlite3.Connection, run_id: str, *, total_items: int) -> None:
    """Debug-only: print action-change counts vs the previous run."""
    prev = conn.execute(
        "SELECT run_id FROM invoice_worklist_history WHERE run_id <> ? ORDER BY id DESC LIMIT 1",
        (run_id,),
    ).fetchone()

    if prev is None:
        print(f"\n[WORKLIST DEBUG] Run {run_id}: {total_items} items (first run)")
        return

    changes = conn.execute("""
        SELECT prev.next_action AS prev_action, curr.next_action AS curr_action, COUNT(*) AS count
        FROM invoice_worklist_history prev
        JOIN invoice_worklist_history curr ON prev.document_hash = curr.document_hash
        WHERE prev.run_id = ? AND curr.run_id = ? AND prev.next_action <> curr.next_action
        GROUP BY prev.next_action, curr.next_action ORDER BY count DESC;
    """, (prev["run_id"], run_id)).fetchall()

    total_changed = sum(r["count"] for r in changes)
    print(f"\n[WORKLIST DEBUG] Run {run_id}: {total_items} items, {total_changed} changed")
    for r in changes:
        print(f"  {r['prev_action']} -> {r['curr_action']}: {r['count']}")


def _values_missing(row: sqlite3.Row) -> bool:
    """True if gross_total is missing. Net/VAT may be NULL without blocking."""
    return row["gross_total"] is None


def _classify_invoice(row: sqlite3.Row) -> Tuple[str, str, int]:
    """Return (next_action, action_reason, priority). Lower priority = earlier in queue."""
    if int(row["is_currently_present"]) == 0:
        return ("NOT CURRENTLY PRESENT", "NOT IN INPUT FOLDER THIS SCAN", 90)

    po_match_status = (row["po_match_status"] or "").strip()
    po_validation_status = (row["po_validation_status"] or "").strip()
    ready_to_post = int(row["ready_to_post"]) if row["ready_to_post"] is not None else 0

    if po_match_status == STATUS_NO_TEXT_LAYER:
        return ("MANUAL REVIEW", "NO TEXT LAYER", 80)
    if po_match_status == STATUS_MISSING_PO:
        return ("MANUAL REVIEW", "MISSING PO", 20)
    if po_match_status == STATUS_MULTIPLE_POS:
        return ("MANUAL REVIEW", "MULTIPLE POS DETECTED", 30)

    if po_match_status == STATUS_SINGLE_PO_DETECTED:
        if po_validation_status == STATUS_PO_NOT_IN_MASTER:
            return ("MANUAL REVIEW", "PO NOT IN MASTER", 40)
        if po_validation_status == STATUS_PO_NOT_OPEN:
            return ("MANUAL REVIEW", "PO NOT OPEN", 20)
        if po_validation_status == STATUS_UNVALIDATED:
            return ("MANUAL REVIEW", "PO NOT VALIDATED YET", 60)
        if po_validation_status == STATUS_PO_NOT_CONFIRMED:
            return ("MANUAL REVIEW", "PO NOT CONFIRMED", 20)
        if po_validation_status not in (STATUS_UNVALIDATED, STATUS_PO_NOT_IN_MASTER, STATUS_PO_NOT_OPEN, STATUS_VALID_PO):
            return ("MANUAL REVIEW", "UNKNOWN PO VALIDATION STATUS", 65)

    # Duplicate suspect gets flagged but doesn't block posting
    duplicate_suspect = int(row["duplicate_suspect"]) if row["duplicate_suspect"] is not None else 0

    if ready_to_post == 1 and duplicate_suspect == 1:
        return ("MANUAL REVIEW", "POSSIBLE DUPLICATE", 15)

    if ready_to_post == 1:
        return ("READY TO POST", "VALID PO", 80)
    if _values_missing(row):
        return ("MANUAL REVIEW", "GROSS TOTAL NOT EXTRACTED", 70)

    return ("MANUAL REVIEW", "UNCLASSIFIED STATE", 85)
