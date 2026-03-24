"""
Duplicate invoice detection for the IRS pipeline.

Flags invoices where the same supplier + same gross total appears more than
once within a configurable window (default 30 days).
"""

from __future__ import annotations

from .db import get_connection

DUPLICATE_WINDOW_DAYS = 30


def _ensure_duplicate_column(conn) -> None:
    """Add duplicate_suspect column if missing."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(invoice_document)").fetchall()]
    if "duplicate_suspect" not in cols:
        conn.execute("ALTER TABLE invoice_document ADD COLUMN duplicate_suspect INTEGER DEFAULT 0")


def run_duplicate_detection() -> dict:
    """
    Detect suspected duplicate invoices.

    A duplicate suspect is an invoice where another invoice exists with:
    - The same supplier_account (via PO match)
    - The same gross_total
    - Within DUPLICATE_WINDOW_DAYS of each other's first_seen_datetime

    The earlier invoice is kept clean; the later one is flagged.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN")
        _ensure_duplicate_column(conn)

        # Reset all flags each run
        conn.execute("UPDATE invoice_document SET duplicate_suspect = 0 WHERE duplicate_suspect = 1")

        # Find duplicates: same supplier + same gross_total within the window
        # Flag the later invoice in each pair
        flagged = conn.execute(
            """
            UPDATE invoice_document
            SET duplicate_suspect = 1
            WHERE document_hash IN (
                SELECT later.document_hash
                FROM invoice_document later
                JOIN invoice_po lp ON later.document_hash = lp.document_hash
                JOIN po_master lpm ON lp.po_number = lpm.po_number
                JOIN invoice_document earlier ON earlier.document_hash != later.document_hash
                    AND earlier.gross_total = later.gross_total
                    AND earlier.gross_total IS NOT NULL
                    AND earlier.first_seen_datetime < later.first_seen_datetime
                JOIN invoice_po ep ON earlier.document_hash = ep.document_hash
                JOIN po_master epm ON ep.po_number = epm.po_number
                    AND epm.supplier_account = lpm.supplier_account
                WHERE later.is_currently_present = 1
                  AND later.gross_total IS NOT NULL
                  AND later.gross_total > 0
                  AND ABS(julianday(later.first_seen_datetime) - julianday(earlier.first_seen_datetime)) <= ?
            )
            """,
            (DUPLICATE_WINDOW_DAYS,),
        ).rowcount

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"duplicates_flagged": flagged}
