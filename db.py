from __future__ import annotations

import sqlite3
from pathlib import Path

"""
Creates tables
enforces constraints
holds connection helper
AND performs lightweight schema migrations (SQLite-friendly).

V1 additions:
- invoice_worklist now carries AP-friendly identifiers for Outlook lookup:
  sender_domain, email_subject, attachment_name, received_datetime
"""

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "inbox.db"

# V1 readiness rule (canonical until you evolve it)
READY_PO_MATCH_STATUSES = ("VALID_PO",)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


# -----------------------------------------------------------------------------
# Schema helpers
# -----------------------------------------------------------------------------
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


# -----------------------------------------------------------------------------
# Migrations
# -----------------------------------------------------------------------------
def ensure_po_validation_column(conn: sqlite3.Connection) -> None:
    """
    Ensure inbox_invoice.po_validation_status exists.

    Lightweight, idempotent schema guard. Uses the shared schema helpers
    so all migrations follow one pattern.
    """
    if not _table_exists(conn, "inbox_invoice"):
        return

    if not _column_exists(conn, "inbox_invoice", "po_validation_status"):
        conn.execute(
            """
            ALTER TABLE inbox_invoice
            ADD COLUMN po_validation_status TEXT NOT NULL DEFAULT 'UNVALIDATED'
            """
        )


def _migrate_add_ready_to_post(conn: sqlite3.Connection) -> None:
    """
    Adds `ready_to_post` to inbox_invoice if missing, and backfills values.
    Safe to run repeatedly.
    """
    if not _table_exists(conn, "inbox_invoice"):
        return

    if not _column_exists(conn, "inbox_invoice", "ready_to_post"):
        # Add column (SQLite supports ADD COLUMN only; can't add CHECK constraints here cleanly)
        conn.execute("ALTER TABLE inbox_invoice ADD COLUMN ready_to_post INTEGER;")

    # Backfill from validation truth (only where NULL, so pipeline can override later)
    conn.execute(
        """
        UPDATE inbox_invoice
        SET ready_to_post = CASE
            WHEN po_validation_status = 'VALID_PO' THEN 1
            ELSE 0
        END
        WHERE ready_to_post IS NULL
        """
    )

    # Defensive normalisation
    conn.execute(
        """
        UPDATE inbox_invoice
        SET ready_to_post = 0
        WHERE ready_to_post IS NOT NULL AND ready_to_post NOT IN (0,1)
        """
    )


def _ensure_ready_index(conn: sqlite3.Connection) -> None:
    """
    Create readiness index AFTER migration guarantees the column exists.
    Safe to run repeatedly.
    """
    if _table_exists(conn, "inbox_invoice") and _column_exists(conn, "inbox_invoice", "ready_to_post"):
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_invoice_ready_present
            ON inbox_invoice (is_currently_present, ready_to_post);
            """
        )


def _migrate_add_worklist_identity_columns(conn: sqlite3.Connection) -> None:
    """
    Adds AP-friendly identifier columns to invoice_worklist and invoice_worklist_history.

    These columns help an AP user locate the invoice in Outlook without access to staging:
    - sender_domain
    - email_subject
    - attachment_name
    - received_datetime

    Safe to run repeatedly.
    """
    identity_cols = (
        ("sender_domain", "TEXT"),
        ("email_subject", "TEXT"),
        ("attachment_name", "TEXT"),
        ("received_datetime", "TEXT"),
    )

    # Current queue table
    if _table_exists(conn, "invoice_worklist"):
        for col, col_type in identity_cols:
            if not _column_exists(conn, "invoice_worklist", col):
                conn.execute(f"ALTER TABLE invoice_worklist ADD COLUMN {col} {col_type};")

    # History table (keep the same shape so the snapshots remain interpretable)
    if _table_exists(conn, "invoice_worklist_history"):
        for col, col_type in identity_cols:
            if not _column_exists(conn, "invoice_worklist_history", col):
                conn.execute(f"ALTER TABLE invoice_worklist_history ADD COLUMN {col} {col_type};")


# -----------------------------------------------------------------------------
# Create schema
# -----------------------------------------------------------------------------
def initialise_database() -> None:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript(
        """
        -- =========================================================
        -- Inbox Message (email = unit of work)
        -- =========================================================
        CREATE TABLE IF NOT EXISTS inbox_message (
            message_id TEXT PRIMARY KEY,

            current_location TEXT NOT NULL,
            first_seen_datetime TEXT NOT NULL,
            last_seen_datetime  TEXT NOT NULL,
            last_scan_datetime  TEXT NOT NULL,

            is_currently_present INTEGER NOT NULL CHECK (is_currently_present IN (0,1)),

            received_datetime TEXT,
            sender_address    TEXT,
            subject           TEXT,

            has_attachments   INTEGER NOT NULL CHECK (has_attachments IN (0,1)),
            attachment_count  INTEGER NOT NULL CHECK (attachment_count >= 0),

            next_step TEXT,
            automation_status TEXT,
            automation_error_detail TEXT,
            last_action_datetime TEXT
        );

        -- =========================================================
        -- Inbox Invoice (attachment/document = unit of scan facts)
        -- =========================================================
        CREATE TABLE IF NOT EXISTS inbox_invoice (
            document_hash TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            attachment_file_name TEXT NOT NULL,

            first_seen_datetime TEXT NOT NULL,
            last_seen_datetime  TEXT NOT NULL,
            last_scan_datetime  TEXT NOT NULL,

            is_currently_present INTEGER NOT NULL CHECK (is_currently_present IN (0,1)),
            source_folder_path TEXT,

            po_count INTEGER NOT NULL CHECK (po_count >= 0),
            po_match_status TEXT NOT NULL,

            -- NEW (V1): canonical readiness flag for dashboard + worklist
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

            FOREIGN KEY (message_id) REFERENCES inbox_message(message_id)
                ON UPDATE CASCADE
                ON DELETE RESTRICT
        );

        -- =========================================================
        -- Detected PO evidence (latest truth per scan)
        -- =========================================================
        CREATE TABLE IF NOT EXISTS invoice_po (
            document_hash TEXT NOT NULL,
            po_number     TEXT NOT NULL,
            detected_datetime TEXT,

            PRIMARY KEY (document_hash, po_number),
            FOREIGN KEY (document_hash) REFERENCES inbox_invoice(document_hash)
                ON UPDATE CASCADE
                ON DELETE CASCADE
        );

        -- =========================================================
        -- PO master snapshot (Dynamics)
        -- =========================================================
        CREATE TABLE IF NOT EXISTS po_master (
            po_number TEXT PRIMARY KEY,
            supplier_account TEXT NOT NULL,
            po_status TEXT,
            last_import_datetime TEXT NOT NULL
        );

        -- =========================================================
        -- Supplier master snapshot (Dynamics)
        -- =========================================================
        CREATE TABLE IF NOT EXISTS supplier_master (
            supplier_account TEXT PRIMARY KEY,
            supplier_name TEXT NOT NULL,
            payment_hold INTEGER CHECK (payment_hold IN (0,1)),
            registered_address TEXT,
            last_import_datetime TEXT NOT NULL
        );

        -- =========================================================
        -- Human resolution (PO selection/override)
        -- =========================================================
        CREATE TABLE IF NOT EXISTS invoice_resolution (
            document_hash TEXT PRIMARY KEY,
            resolve_po_number TEXT,
            resolution_status TEXT NOT NULL,
            resolved_by TEXT,
            resolved_datetime TEXT,
            resolution_note TEXT,

            FOREIGN KEY (document_hash) REFERENCES inbox_invoice(document_hash)
                ON UPDATE CASCADE
                ON DELETE CASCADE
        );

        -- =========================================================
        -- Worklist (derived queue cache + append-only history)
        -- =========================================================

        -- Current computed queue (one row per invoice)
        CREATE TABLE IF NOT EXISTS invoice_worklist (
            document_hash TEXT PRIMARY KEY,

            -- AP-friendly identifiers for locating the invoice in Outlook
            sender_domain TEXT,
            email_subject TEXT,
            attachment_name TEXT,
            received_datetime TEXT,

            next_action TEXT NOT NULL,
            action_reason TEXT NOT NULL,
            priority INTEGER NOT NULL,
            generated_at_utc TEXT NOT NULL,
            is_currently_present INTEGER NOT NULL CHECK (is_currently_present IN (0,1)),

            FOREIGN KEY (document_hash) REFERENCES inbox_invoice(document_hash)
                ON UPDATE CASCADE
                ON DELETE CASCADE
        );

        -- Append-only snapshots (truthy history of what the queue looked like per run)
        CREATE TABLE IF NOT EXISTS invoice_worklist_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            document_hash TEXT NOT NULL,

            -- AP-friendly identifiers for locating the invoice in Outlook
            sender_domain TEXT,
            email_subject TEXT,
            attachment_name TEXT,
            received_datetime TEXT,

            next_action TEXT NOT NULL,
            action_reason TEXT NOT NULL,
            priority INTEGER NOT NULL,
            generated_at_utc TEXT NOT NULL,
            is_currently_present INTEGER NOT NULL CHECK (is_currently_present IN (0,1)),

            FOREIGN KEY (document_hash) REFERENCES inbox_invoice(document_hash)
                ON UPDATE CASCADE
                ON DELETE CASCADE
        );

        -- =========================================================
        -- Indexes (dashboard + worklist)
        -- NOTE: do NOT create indexes here that reference columns
        -- that might not exist yet in an older DB.
        -- =========================================================
        CREATE INDEX IF NOT EXISTS idx_message_location
        ON inbox_message (current_location, is_currently_present);

        CREATE INDEX IF NOT EXISTS idx_message_next_step
        ON inbox_message (next_step);

        CREATE INDEX IF NOT EXISTS idx_message_received
        ON inbox_message (received_datetime);

        CREATE INDEX IF NOT EXISTS idx_invoice_message
        ON inbox_invoice (message_id);

        CREATE INDEX IF NOT EXISTS idx_invoice_presence
        ON inbox_invoice (is_currently_present, processing_status);

        CREATE INDEX IF NOT EXISTS idx_invoice_po_count_status
        ON inbox_invoice (po_count, po_match_status);

        CREATE INDEX IF NOT EXISTS idx_invoice_supplier_status
        ON inbox_invoice (supplier_account_expected, supplier_validation_status);

        CREATE INDEX IF NOT EXISTS idx_invoice_posting
        ON inbox_invoice (processing_status, posted_datetime);

        CREATE INDEX IF NOT EXISTS idx_invoice_po_po
        ON invoice_po (po_number);

        CREATE INDEX IF NOT EXISTS idx_po_supplier
        ON po_master (supplier_account);

        CREATE INDEX IF NOT EXISTS idx_worklist_action
        ON invoice_worklist (next_action, priority);

        CREATE INDEX IF NOT EXISTS idx_worklist_present
        ON invoice_worklist (is_currently_present, priority);

        CREATE INDEX IF NOT EXISTS idx_worklist_hist_run
        ON invoice_worklist_history (run_id);

        CREATE INDEX IF NOT EXISTS idx_worklist_hist_doc
        ON invoice_worklist_history (document_hash);

        CREATE UNIQUE INDEX IF NOT EXISTS uq_worklist_hist_run_doc
        ON invoice_worklist_history (run_id, document_hash);
        """
    )

    # Migrations (must run after base schema exists)
    ensure_po_validation_column(conn)
    _migrate_add_ready_to_post(conn)
    _migrate_add_worklist_identity_columns(conn)

    # Dependent indexes (must run after migrations)
    _ensure_ready_index(conn)

    conn.commit()
    conn.close()


def reset_database() -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.executescript(
        """
        DROP TABLE IF EXISTS invoice_worklist_history;
        DROP TABLE IF EXISTS invoice_worklist;
        DROP TABLE IF EXISTS invoice_resolution;
        DROP TABLE IF EXISTS invoice_po;
        DROP TABLE IF EXISTS inbox_invoice;
        DROP TABLE IF EXISTS inbox_message;
        DROP TABLE IF EXISTS po_master;
        DROP TABLE IF EXISTS supplier_master;
        """
    )
    conn.commit()
    conn.close()
