from __future__ import annotations

"""
Database schema, connection helper, and idempotent migrations for the IRS pipeline.
"""

import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "irs.db"

READY_PO_MATCH_STATUSES = ("VALID_PO",)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1;",
        (name,),
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not _table_exists(conn, table):
        return False
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return any(r["name"] == column for r in rows)


def ensure_po_validation_column(conn: sqlite3.Connection) -> None:
    """Add po_validation_status column to invoice_document if missing."""
    if not _table_exists(conn, "invoice_document"):
        return
    if not _column_exists(conn, "invoice_document", "po_validation_status"):
        conn.execute(
            "ALTER TABLE invoice_document ADD COLUMN po_validation_status TEXT NOT NULL DEFAULT 'UNVALIDATED'"
        )


def _migrate_add_ready_to_post(conn: sqlite3.Connection) -> None:
    """Add ready_to_post column to invoice_document if missing, and backfill."""
    if not _table_exists(conn, "invoice_document"):
        return
    if not _column_exists(conn, "invoice_document", "ready_to_post"):
        conn.execute("ALTER TABLE invoice_document ADD COLUMN ready_to_post INTEGER;")
    conn.execute("""
        UPDATE invoice_document
        SET ready_to_post = CASE WHEN po_validation_status = 'VALID_PO' THEN 1 ELSE 0 END
        WHERE ready_to_post IS NULL
    """)
    conn.execute("""
        UPDATE invoice_document SET ready_to_post = 0
        WHERE ready_to_post IS NOT NULL AND ready_to_post NOT IN (0,1)
    """)


def _ensure_ready_index(conn: sqlite3.Connection) -> None:
    """Create readiness index after migration guarantees the column exists."""
    if _table_exists(conn, "invoice_document") and _column_exists(conn, "invoice_document", "ready_to_post"):
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_document_ready_present
            ON invoice_document (is_currently_present, ready_to_post);
        """)


def ensure_po_master_approval_column(conn: sqlite3.Connection) -> None:
    """Add approval_status column to po_master if missing."""
    if not _table_exists(conn, "po_master"):
        return
    if not _column_exists(conn, "po_master", "approval_status"):
        conn.execute("ALTER TABLE po_master ADD COLUMN approval_status TEXT;")


def initialise_database() -> None:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript("""
        -- Ingestion record (one per scanned PDF source)
        CREATE TABLE IF NOT EXISTS invoice_file (
            file_id TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            first_seen_datetime TEXT NOT NULL,
            last_seen_datetime  TEXT NOT NULL,
            last_scan_datetime  TEXT NOT NULL,
            is_currently_present INTEGER NOT NULL CHECK (is_currently_present IN (0,1)),
            scanned_datetime TEXT,
            file_name TEXT
        );

        -- Invoice document (keyed by SHA-256 hash of the PDF)
        CREATE TABLE IF NOT EXISTS invoice_document (
            document_hash TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            file_name TEXT NOT NULL,
            first_seen_datetime TEXT NOT NULL,
            last_seen_datetime  TEXT NOT NULL,
            last_scan_datetime  TEXT NOT NULL,
            is_currently_present INTEGER NOT NULL CHECK (is_currently_present IN (0,1)),
            source_folder_path TEXT,
            po_count INTEGER NOT NULL CHECK (po_count >= 0),
            po_match_status TEXT NOT NULL,
            ready_to_post INTEGER NOT NULL DEFAULT 0 CHECK (ready_to_post IN (0,1)),
            supplier_account_expected   TEXT,
            supplier_validation_status  TEXT,
            processing_status TEXT NOT NULL,
            posted_datetime   TEXT,
            net_total   INTEGER,
            vat_total   INTEGER,
            gross_total INTEGER,
            review_outcome    TEXT,
            reviewed_datetime TEXT,
            reviewed_by       TEXT,
            review_note       TEXT,
            FOREIGN KEY (file_id) REFERENCES invoice_file(file_id)
                ON UPDATE CASCADE ON DELETE RESTRICT
        );

        -- Detected PO evidence
        CREATE TABLE IF NOT EXISTS invoice_po (
            document_hash TEXT NOT NULL,
            po_number     TEXT NOT NULL,
            detected_datetime TEXT,
            PRIMARY KEY (document_hash, po_number),
            FOREIGN KEY (document_hash) REFERENCES invoice_document(document_hash)
                ON UPDATE CASCADE ON DELETE CASCADE
        );

        -- PO master (loaded from CSV/XLSX upload)
        CREATE TABLE IF NOT EXISTS po_master (
            po_number TEXT PRIMARY KEY,
            supplier_account TEXT NOT NULL,
            po_status TEXT,
            approval_status TEXT,
            last_import_datetime TEXT NOT NULL
        );

        -- Supplier master (reserved for future use)
        CREATE TABLE IF NOT EXISTS supplier_master (
            supplier_account TEXT PRIMARY KEY,
            supplier_name TEXT NOT NULL,
            payment_hold INTEGER CHECK (payment_hold IN (0,1)),
            registered_address TEXT,
            last_import_datetime TEXT NOT NULL
        );

        -- Human resolution (PO selection/override)
        CREATE TABLE IF NOT EXISTS invoice_resolution (
            document_hash TEXT PRIMARY KEY,
            resolve_po_number TEXT,
            resolution_status TEXT NOT NULL,
            resolved_by TEXT,
            resolved_datetime TEXT,
            resolution_note TEXT,
            FOREIGN KEY (document_hash) REFERENCES invoice_document(document_hash)
                ON UPDATE CASCADE ON DELETE CASCADE
        );

        -- Worklist (current computed queue)
        CREATE TABLE IF NOT EXISTS invoice_worklist (
            document_hash TEXT PRIMARY KEY,
            file_name TEXT,
            scanned_datetime TEXT,
            next_action TEXT NOT NULL,
            action_reason TEXT NOT NULL,
            priority INTEGER NOT NULL,
            generated_at_utc TEXT NOT NULL,
            is_currently_present INTEGER NOT NULL CHECK (is_currently_present IN (0,1)),
            FOREIGN KEY (document_hash) REFERENCES invoice_document(document_hash)
                ON UPDATE CASCADE ON DELETE CASCADE
        );

        -- Worklist history (append-only snapshots per run)
        CREATE TABLE IF NOT EXISTS invoice_worklist_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            document_hash TEXT NOT NULL,
            file_name TEXT,
            scanned_datetime TEXT,
            next_action TEXT NOT NULL,
            action_reason TEXT NOT NULL,
            priority INTEGER NOT NULL,
            generated_at_utc TEXT NOT NULL,
            is_currently_present INTEGER NOT NULL CHECK (is_currently_present IN (0,1)),
            FOREIGN KEY (document_hash) REFERENCES invoice_document(document_hash)
                ON UPDATE CASCADE ON DELETE CASCADE
        );

        -- Indexes
        CREATE INDEX IF NOT EXISTS idx_file_source ON invoice_file (source_path, is_currently_present);
        CREATE INDEX IF NOT EXISTS idx_file_scanned ON invoice_file (scanned_datetime);
        CREATE INDEX IF NOT EXISTS idx_document_file ON invoice_document (file_id);
        CREATE INDEX IF NOT EXISTS idx_document_presence ON invoice_document (is_currently_present, processing_status);
        CREATE INDEX IF NOT EXISTS idx_document_po_count_status ON invoice_document (po_count, po_match_status);
        CREATE INDEX IF NOT EXISTS idx_document_supplier_status ON invoice_document (supplier_account_expected, supplier_validation_status);
        CREATE INDEX IF NOT EXISTS idx_document_posting ON invoice_document (processing_status, posted_datetime);
        CREATE INDEX IF NOT EXISTS idx_invoice_po_po ON invoice_po (po_number);
        CREATE INDEX IF NOT EXISTS idx_po_supplier ON po_master (supplier_account);
        CREATE INDEX IF NOT EXISTS idx_worklist_action ON invoice_worklist (next_action, priority);
        CREATE INDEX IF NOT EXISTS idx_worklist_present ON invoice_worklist (is_currently_present, priority);
        CREATE INDEX IF NOT EXISTS idx_worklist_hist_run ON invoice_worklist_history (run_id);
        CREATE INDEX IF NOT EXISTS idx_worklist_hist_doc ON invoice_worklist_history (document_hash);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_worklist_hist_run_doc ON invoice_worklist_history (run_id, document_hash);
    """)

    ensure_po_master_approval_column(conn)
    ensure_po_validation_column(conn)
    _migrate_add_ready_to_post(conn)
    _ensure_ready_index(conn)

    conn.commit()
    conn.close()


def reset_database() -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.executescript("""
        DROP TABLE IF EXISTS invoice_worklist_history;
        DROP TABLE IF EXISTS invoice_worklist;
        DROP TABLE IF EXISTS invoice_resolution;
        DROP TABLE IF EXISTS invoice_po;
        DROP TABLE IF EXISTS invoice_document;
        DROP TABLE IF EXISTS invoice_file;
        DROP TABLE IF EXISTS po_master;
        DROP TABLE IF EXISTS supplier_master;
    """)
    conn.commit()
    conn.close()
