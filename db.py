import sqlite3
from pathlib import Path

'''
Creates tables

enforces constraints

holds connection helper
'''
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

    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS inbox_invoice (
        inbox_id INTEGER PRIMARY KEY AUTOINCREMENT,

        -- Technical fingerprint (unique identity of the PDF document)
        document_hash TEXT NOT NULL UNIQUE,

        -- AP-facing identifiers (may be missing or non-unique globally)
        invoice_id TEXT,  -- invoice number/reference from invoice (nullable)

        file_name TEXT NOT NULL,

        first_seen_datetime TEXT NOT NULL,
        last_seen_datetime TEXT NOT NULL,
        last_scan_datetime TEXT NOT NULL,

        is_currently_present INTEGER NOT NULL CHECK (is_currently_present IN (0,1)),

        po_count INTEGER NOT NULL,
        match_status TEXT NOT NULL,

        supplier_account_expected TEXT,
        supplier_validation_status TEXT,

        processing_status TEXT NOT NULL,
        posted_datetime TEXT,

        currency TEXT,
        net_amount_minor INTEGER,
        vat_amount_minor INTEGER,
        gross_amount_minor INTEGER,

        scan_error_code TEXT,
        scan_error_detail TEXT
    );

    CREATE TABLE IF NOT EXISTS invoice_po (
        document_hash TEXT NOT NULL,
        po_number TEXT NOT NULL,

        PRIMARY KEY (document_hash, po_number),
        FOREIGN KEY (document_hash) REFERENCES inbox_invoice(document_hash) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS po_master (
        po_number TEXT PRIMARY KEY,
        supplier_account TEXT NOT NULL,
        po_status TEXT,
        last_import_datetime TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS supplier_master (
        supplier_account TEXT PRIMARY KEY,
        supplier_name TEXT NOT NULL,
        status TEXT,
        payment_hold INTEGER CHECK (payment_hold IN (0,1)),
        last_import_datetime TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_inbox_status
    ON inbox_invoice (processing_status, is_currently_present);

    CREATE INDEX IF NOT EXISTS idx_invoice_id
    ON inbox_invoice (invoice_id);

    CREATE INDEX IF NOT EXISTS idx_invoice_po_po
    ON invoice_po (po_number);

    CREATE INDEX IF NOT EXISTS idx_po_supplier
    ON po_master (supplier_account);
    """)

    conn.commit()
    conn.close()
