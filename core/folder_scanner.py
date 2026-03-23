from __future__ import annotations

"""
Folder scanner for the IRS pipeline.

Scans a local directory for PDF files, copies them to staging/,
computes SHA-256 hashes, and persists scan results into SQLite.
"""

import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .db import get_connection, initialise_database
from .fingerprint import sha256_file

BASE_DIR = Path(__file__).resolve().parent.parent
STAGING_DIR = BASE_DIR / "staging"
STAGING_DIR.mkdir(exist_ok=True)

_ENV_DEBUG = os.getenv("ICS_DEBUG", "").strip().lower()
DEBUG = _ENV_DEBUG in ("1", "true", "yes", "y", "on")


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_filename(name: str) -> str:
    name = re.sub(r"[^\w\-. ]+", "_", name).strip()
    return name or "document.pdf"


def begin_scan(conn, scan_ts: str) -> None:
    cur = conn.cursor()
    cur.execute("UPDATE invoice_file SET is_currently_present = 0")
    cur.execute("UPDATE invoice_document SET is_currently_present = 0")


def upsert_file(
    conn,
    *,
    file_id: str,
    source_path: str,
    scan_ts: str,
    scanned_datetime: Optional[str],
    file_name: Optional[str],
) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO invoice_file (
            file_id, source_path, first_seen_datetime, last_seen_datetime,
            last_scan_datetime, is_currently_present, scanned_datetime, file_name
        )
        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(file_id) DO UPDATE SET
            source_path          = excluded.source_path,
            last_seen_datetime   = excluded.last_seen_datetime,
            last_scan_datetime   = excluded.last_scan_datetime,
            is_currently_present = 1,
            scanned_datetime     = excluded.scanned_datetime,
            file_name            = excluded.file_name
        """,
        (file_id, source_path, scan_ts, scan_ts, scan_ts, scanned_datetime, file_name),
    )


def upsert_document(
    conn,
    *,
    document_hash: str,
    file_id: str,
    file_name: str,
    scan_ts: str,
    source_folder_path: str,
) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO invoice_document (
            document_hash, file_id, file_name,
            first_seen_datetime, last_seen_datetime, last_scan_datetime,
            is_currently_present, source_folder_path,
            po_count, po_match_status,
            supplier_account_expected, supplier_validation_status,
            processing_status, posted_datetime,
            net_total, vat_total, gross_total,
            review_outcome, reviewed_datetime, reviewed_by, review_note
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, 0, 'UNSCANNED', NULL, NULL, 'NEW', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)
        ON CONFLICT(document_hash) DO UPDATE SET
            file_id              = excluded.file_id,
            file_name            = excluded.file_name,
            last_seen_datetime   = excluded.last_seen_datetime,
            last_scan_datetime   = excluded.last_scan_datetime,
            is_currently_present = 1,
            source_folder_path   = excluded.source_folder_path
        """,
        (document_hash, file_id, file_name, scan_ts, scan_ts, scan_ts, source_folder_path),
    )


def scan_folder_to_db(input_dir: Path) -> dict:
    """Scan a local folder for PDF files, copy to staging, and persist metadata to SQLite."""
    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")

    initialise_database()

    scan_ts = now_iso_utc()
    pdfs_seen = 0
    pdfs_saved = 0

    pdf_files = sorted(input_dir.rglob("*.pdf"))

    conn = get_connection()
    try:
        conn.execute("BEGIN")
        begin_scan(conn, scan_ts)

        for pdf_path in pdf_files:
            pdfs_seen += 1
            doc_hash = sha256_file(pdf_path)

            safe_name = safe_filename(pdf_path.name)
            staging_name = f"{doc_hash[:12]}_01_{safe_name}"
            staging_path = STAGING_DIR / staging_name

            try:
                tmp_path = staging_path.with_suffix(".tmp")
                shutil.copy2(pdf_path, tmp_path)
                tmp_path.rename(staging_path)
            except (FileExistsError, OSError):
                if tmp_path.exists():
                    tmp_path.unlink()

            pdfs_saved += 1

            file_id = f"folder_{doc_hash}"
            folder_path = str(pdf_path.parent)

            upsert_file(
                conn,
                file_id=file_id,
                source_path=folder_path,
                scan_ts=scan_ts,
                scanned_datetime=scan_ts,
                file_name=pdf_path.name,
            )

            upsert_document(
                conn,
                document_hash=doc_hash,
                file_id=file_id,
                file_name=pdf_path.name,
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
