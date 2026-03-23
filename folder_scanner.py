from __future__ import annotations

"""
Folder Scanner

Drop-in replacement for outlook_scanner.py.
Scans a local directory for PDF files, copies them to staging/,
computes SHA-256 hashes, and persists scan results into SQLite.

Same database tables, same columns, same return shape.
"""

import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from db import get_connection, initialise_database
from fingerprint import sha256_file


# ----------------------------
# Config
# ----------------------------

BASE_DIR = Path(__file__).resolve().parent
STAGING_DIR = BASE_DIR / "staging"
STAGING_DIR.mkdir(exist_ok=True)

# Debug toggle
_ENV_DEBUG = os.getenv("ICS_DEBUG", "").strip().lower()
DEBUG = _ENV_DEBUG in ("1", "true", "yes", "y", "on")


# ----------------------------
# Helpers
# ----------------------------

def now_iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_filename(name: str) -> str:
    name = re.sub(r"[^\w\-. ]+", "_", name).strip()
    return name or "attachment.pdf"


# ----------------------------
# DB helpers (presence + upserts)
# ----------------------------

def begin_scan(conn, scan_ts: str) -> None:
    cur = conn.cursor()
    cur.execute("UPDATE inbox_message SET is_currently_present = 0")
    cur.execute("UPDATE inbox_invoice SET is_currently_present = 0")


def upsert_message(
    conn,
    *,
    message_id: str,
    current_location: str,
    scan_ts: str,
    received_datetime: Optional[str],
    sender_address: Optional[str],
    subject: Optional[str],
    has_attachments: bool,
    attachment_count: int,
) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO inbox_message (
            message_id,
            current_location,
            first_seen_datetime,
            last_seen_datetime,
            last_scan_datetime,
            is_currently_present,
            received_datetime,
            sender_address,
            subject,
            has_attachments,
            attachment_count,
            next_step,
            automation_status,
            automation_error_detail,
            last_action_datetime
        )
        VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)
        ON CONFLICT(message_id) DO UPDATE SET
            current_location      = excluded.current_location,
            last_seen_datetime    = excluded.last_seen_datetime,
            last_scan_datetime    = excluded.last_scan_datetime,
            is_currently_present  = 1,
            received_datetime     = excluded.received_datetime,
            sender_address        = excluded.sender_address,
            subject               = excluded.subject,
            has_attachments       = excluded.has_attachments,
            attachment_count      = excluded.attachment_count
        """,
        (
            message_id,
            current_location,
            scan_ts,
            scan_ts,
            scan_ts,
            received_datetime,
            sender_address,
            subject,
            1 if has_attachments else 0,
            int(attachment_count),
        ),
    )


def upsert_invoice(
    conn,
    *,
    document_hash: str,
    message_id: str,
    attachment_file_name: str,
    scan_ts: str,
    source_folder_path: str,
) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO inbox_invoice (
            document_hash,
            message_id,
            attachment_file_name,
            first_seen_datetime,
            last_seen_datetime,
            last_scan_datetime,
            is_currently_present,
            source_folder_path,
            po_count,
            po_match_status,
            supplier_account_expected,
            supplier_validation_status,
            processing_status,
            posted_datetime,
            net_total,
            vat_total,
            gross_total,
            review_outcome,
            reviewed_datetime,
            reviewed_by,
            review_note
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, 0, 'UNSCANNED', NULL, NULL, 'NEW', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)
        ON CONFLICT(document_hash) DO UPDATE SET
            message_id            = excluded.message_id,
            attachment_file_name  = excluded.attachment_file_name,
            last_seen_datetime    = excluded.last_seen_datetime,
            last_scan_datetime    = excluded.last_scan_datetime,
            is_currently_present  = 1,
            source_folder_path    = excluded.source_folder_path
        """,
        (
            document_hash,
            message_id,
            attachment_file_name,
            scan_ts,
            scan_ts,
            scan_ts,
            source_folder_path,
        ),
    )


# ----------------------------
# Main scan function
# ----------------------------

def scan_folder_to_db(input_dir: Path) -> dict:
    """
    Scan a local folder for PDF files and persist results into SQLite.

    Drop-in replacement for scan_outlook_to_db().
    Returns the same summary dict shape: messages_seen, pdfs_saved, staging_dir.
    """
    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")

    initialise_database()

    scan_ts = now_iso_utc()
    pdfs_seen = 0
    pdfs_saved = 0

    # Collect all PDFs recursively
    pdf_files = sorted(input_dir.rglob("*.pdf"))

    conn = get_connection()
    try:
        conn.execute("BEGIN")
        begin_scan(conn, scan_ts)

        for pdf_path in pdf_files:
            pdfs_seen += 1

            # Hash the source file to check for duplicates
            doc_hash = sha256_file(pdf_path)

            # Copy to staging using a safe filename convention
            safe_name = safe_filename(pdf_path.name)
            # Use first 12 chars of hash as prefix (mirrors the EntryID short prefix)
            staging_name = f"{doc_hash[:12]}_01_{safe_name}"
            staging_path = STAGING_DIR / staging_name

            try:
                # Copy only if not already staged; use exclusive create via
                # a temp file + rename to avoid TOCTOU races.
                tmp_path = staging_path.with_suffix(".tmp")
                shutil.copy2(pdf_path, tmp_path)
                tmp_path.rename(staging_path)
            except (FileExistsError, OSError):
                # Already staged or rename collision — safe to skip
                if tmp_path.exists():
                    tmp_path.unlink()


            pdfs_saved += 1

            # Use the document hash as the message_id (one PDF = one "message")
            message_id = f"folder_{doc_hash}"
            folder_path = str(pdf_path.parent)

            upsert_message(
                conn,
                message_id=message_id,
                current_location=folder_path,
                scan_ts=scan_ts,
                received_datetime=scan_ts,
                sender_address=None,
                subject=pdf_path.name,
                has_attachments=True,
                attachment_count=1,
            )

            upsert_invoice(
                conn,
                document_hash=doc_hash,
                message_id=message_id,
                attachment_file_name=pdf_path.name,
                scan_ts=scan_ts,
                source_folder_path=folder_path,
            )

        conn.commit()

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "messages_seen": pdfs_seen,
        "pdfs_saved": pdfs_saved,
        "staging_dir": str(STAGING_DIR),
        "scan_ts": scan_ts,
    }


if __name__ == "__main__":
    default_input = BASE_DIR / "input"
    default_input.mkdir(exist_ok=True)
    result = scan_folder_to_db(default_input)
    print(result)
