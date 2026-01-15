import sqlite3
from pathlib import Path

"""
Creates tables
enforces constraints
holds connection helper
"""

DB_PATH = Path(__file__).resolve().parent / "inbox.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Enforce Foreign Key constraints in SQLite (off by default)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


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

            current_location TEXT NOT NULL,                 -- latest seen folder path (or folder id later)
            first_seen_datetime TEXT NOT NULL,
            last_seen_datetime  TEXT NOT NULL,
            last_scan_datetime  TEXT NOT NULL,

            is_currently_present INTEGER NOT NULL CHECK (is_currently_present IN (0,1)),

            received_datetime TEXT,                          -- from Outlook (nullable)
            sender_address    TEXT,                          -- nullable
            subject           TEXT,                          -- nullable

            has_attachments   INTEGER NOT NULL CHECK (has_attachments IN (0,1)),
            attachment_count  INTEGER NOT NULL CHECK (attachment_count >= 0),

            next_step TEXT,                                  -- POST_TO_ERP / REJECT / MANUAL_CHECK (nullable until decided)
            automation_status TEXT,                           -- PENDING / DONE / FAILED (nullable)
            automation_error_detail TEXT,                     -- nullable
            last_action_datetime TEXT                         -- nullable
        );

        -- =========================================================
        -- Inbox Invoice (attachment/document = unit of scan facts)
        -- =========================================================
        CREATE TABLE IF NOT EXISTS inbox_invoice (
            -- Technical fingerprint (unique identity of the PDF bytes)
            document_hash TEXT PRIMARY KEY,

            -- Link to the email message where this document was most recently seen
            message_id TEXT NOT NULL,

            attachment_file_name TEXT NOT NULL,

            first_seen_datetime TEXT NOT NULL,
            last_seen_datetime  TEXT NOT NULL,
            last_scan_datetime  TEXT NOT NULL,

            is_currently_present INTEGER NOT NULL CHECK (is_currently_present IN (0,1)),

            -- Latest seen email folder path (duplicated for convenience; message table is source-of-truth)
            source_folder_path TEXT,

            po_count INTEGER NOT NULL CHECK (po_count >= 0),
            po_match_status TEXT NOT NULL,                   -- e.g. VALID_PO / INVALID_PO / MISSING_PO / MULTIPLE_POS / NO_TEXT_LAYER

            supplier_account_expected   TEXT,
            supplier_validation_status  TEXT,

            processing_status TEXT NOT NULL,                 -- NEW / POSTED
            posted_datetime   TEXT,                          -- nullable

            net_total   INTEGER,                             -- minor units recommended (nullable)
            vat_total   INTEGER,                             -- minor units recommended (nullable)
            gross_total INTEGER,                             -- minor units recommended (nullable)

            review_outcome    TEXT,                          -- APPROVE / REJECT / FLAG / UNREVIEWED (nullable)
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
            detected_datetime TEXT,                           -- optional; can mirror scan time

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
            document_hash TEXT PRIMARY KEY,                   -- one resolution per document (latest truth)
            resolve_po_number TEXT,
            resolution_status TEXT NOT NULL,                  -- RESOLVED / UNRESOLVED (or your preferred set)
            resolved_by TEXT,
            resolved_datetime TEXT,
            resolution_note TEXT,

            FOREIGN KEY (document_hash) REFERENCES inbox_invoice(document_hash)
                ON UPDATE CASCADE
                ON DELETE CASCADE
        );

        -- =========================================================
        -- Indexes (dashboard + worklist)
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
        """
    )

    conn.commit()
    conn.close()

def reset_database() -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.executescript("""
    DROP TABLE IF EXISTS invoice_resolution;
    DROP TABLE IF EXISTS invoice_po;
    DROP TABLE IF EXISTS inbox_invoice;
    DROP TABLE IF EXISTS inbox_message;
    DROP TABLE IF EXISTS po_master;
    DROP TABLE IF EXISTS supplier_master;
    """)
    conn.commit()
    conn.close()