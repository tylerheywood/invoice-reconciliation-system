import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
import uuid
from typing import Optional, Tuple, List, Dict, Any


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
    # Short, readable, still unique enough for V1
    return uuid.uuid4().hex


def build_worklist(
    conn: sqlite3.Connection,
    *,
    only_currently_present: bool = True,
    include_ready_to_post: bool = True,
) -> Tuple[str, List[WorkItem]]:
    """
    Compute the current worklist deterministically from inbox_invoice truth columns
    and return (run_id, items). Does not write to DB.

    V1 rules:
    - precedence based (first blocker wins)
    - deterministic
    - no hidden state
    """
    generated_at_utc = _utc_now_iso()
    run_id = _new_run_id()

    where_clause = ""
    params: List[Any] = []
    if only_currently_present:
        where_clause = "WHERE ii.is_currently_present = 1"

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
        """,
        params,
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
    Full-replace refresh of invoice_worklist, plus append-only snapshot to history.

    This is the V1 "B" model:
    - No manual removal
    - Worklist reflects current truth
    - History is append-only snapshots per run_id
    """
    run_id, items = build_worklist(
        conn,
        only_currently_present=only_currently_present,
        include_ready_to_post=include_ready_to_post,
    )

    # Single transaction: either the refresh completes or nothing changes
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

    return run_id


# ----------------------------
# Classification rules (V1)
# ----------------------------

def _values_missing(row: sqlite3.Row) -> bool:
    # V1: keep simple and deterministic.
    # "VAT nuance later" => we do not interpret VAT; we just check presence.
    return (
        row["net_total"] is None
        or row["vat_total"] is None
        or row["gross_total"] is None
    )


def _classify_invoice(row: sqlite3.Row) -> Tuple[str, str, int]:
    """
    Return (next_action, action_reason, priority).

    PRIORITY: lower number = higher priority (appears first).
    Keep this mapping explicit and stable.
    """

    # If not present, it's not actionable in inbox terms (still useful for reconciliation)
    if int(row["is_currently_present"]) == 0:
        return ("NOT CURRENTLY PRESENT", "NOT IN INBOX THIS SCAN", 90)

    po_match_status = (row["po_match_status"] or "").strip()
    po_validation_status = (row["po_validation_status"] or "").strip()
    ready_to_post = int(row["ready_to_post"]) if row["ready_to_post"] is not None else 0

    # 1) No text layer (scanned/blocked)
    if po_match_status == "NO_TEXT_LAYER":
        return ("MANUAL REVIEW", "NO TEXT LAYER", 10)

    # 2) PO missing
    if po_match_status == "MISSING_PO":
        return ("REQUEST PO", "MISSING PO", 20)

    # 3) Multiple POs
    if po_match_status == "MULTIPLE_POS":
        return ("SELECT CORRECT PO", "MULTIPLE POS DETECTED", 30)

    # 4) Single PO detected but not valid in master / status blocks
    if po_match_status == "SINGLE_PO_DETECTED":
        if po_validation_status == "PO_NOT_IN_MASTER":
            return ("CHECK PO MASTER", "PO NOT IN MASTER", 40)
        if po_validation_status == "PO_NOT_OPEN":
            return ("CHECK PO STATUS", "PO NOT OPEN", 50)
        if po_validation_status == "UNVALIDATED":
            # Pipeline should usually resolve this, but keep it explicit
            return ("MANUAL REVIEW", "PO NOT VALIDATED YET", 60)

    # 5) Values missing (only if we can read text)
    if _values_missing(row):
        return ("ENTER / CONFIRM VALUE", "TOTALS NOT EXTRACTED", 70)

    # 6) Ready to post
    if ready_to_post == 1:
        return ("READY TO POST", "VALID PO + VALUES PRESENT", 80)

    # 7) Catch-all
    return ("MANUAL REVIEW", "UNCLASSIFIED STATE", 85)


def fetch_current_worklist(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    Convenience reader for dashboard/CLI: returns dict rows from invoice_worklist,
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
