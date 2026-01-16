from __future__ import annotations

"""
Outlook Scanner

Connect to Outlook + list messages deterministically
List PDF *file* attachments only (ignore inline/signature images)
Save PDF attachments to staging + compute document_hash (SHA-256)
Persist scan results into SQLite (inbox_message + inbox_invoice)

Notes:
- Email (message) is the unit of work: inbox_message
- PDF attachment is the unit of document facts: inbox_invoice
- Read-only against Outlook (no moving emails yet)
- Deterministic + auditable
"""
from po_detection import run_po_detection
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import win32com.client  # pip install pywin32

from db import get_connection, initialise_database
from fingerprint import sha256_file


# ----------------------------
# Config
# ----------------------------

MAILBOX_NAME = "tyler@aphospital.co.uk"
FOLDER_PATH = "Inbox"  # e.g. "Inbox/Invoices/New"
MAX_ITEMS = 50

BASE_DIR = Path(__file__).resolve().parent
STAGING_DIR = BASE_DIR / "staging"
STAGING_DIR.mkdir(exist_ok=True)


# ----------------------------
# Models
# ----------------------------

@dataclass(frozen=True)
class PdfAttachment:
    attachment_index: int
    file_name: str
    size_bytes: int
    save_path: Path
    document_hash: str


# ----------------------------
# Time / formatting helpers
# ----------------------------

def now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_filename(name: str) -> str:
    # Windows-safe, deterministic. Keep extension. Replace weird chars.
    name = re.sub(r"[^\w\-. ]+", "_", name).strip()
    return name or "attachment.pdf"


def short_entry_id(entry_id: str, n: int = 12) -> str:
    # EntryID is huge; last N chars are enough to prevent collisions for local naming.
    return entry_id[-n:] if len(entry_id) >= n else entry_id


# ----------------------------
# Outlook helpers
# ----------------------------

def get_outlook_namespace():
    outlook = win32com.client.Dispatch("Outlook.Application")
    return outlook.GetNamespace("MAPI")


def list_mailboxes(ns) -> list[str]:
    names: list[str] = []
    for i in range(1, ns.Folders.Count + 1):
        names.append(str(ns.Folders.Item(i).Name))
    return names


def get_mailbox(ns, mailbox_name: str):
    for i in range(1, ns.Folders.Count + 1):
        store = ns.Folders.Item(i)
        if str(store.Name).strip().lower() == mailbox_name.strip().lower():
            return store

    available = ", ".join(list_mailboxes(ns))
    raise ValueError(f"Mailbox '{mailbox_name}' not found. Available: {available}")


def get_folder_by_path(store, folder_path: str):
    parts = [p.strip() for p in folder_path.split("/") if p.strip()]
    if not parts:
        raise ValueError("FOLDER_PATH is empty")

    folder = store.Folders.Item(parts[0])
    for part in parts[1:]:
        folder = folder.Folders.Item(part)
    return folder


def iter_latest_messages(folder, max_items: int):
    items = folder.Items
    items.Sort("[ReceivedTime]", True)  # True = descending
    count = min(max_items, items.Count)
    for i in range(1, count + 1):
        yield items.Item(i)


def safe_sender_email(mail_item) -> str:
    # Some Exchange senders come back as X500/EXCHANGELABS paths; we’ll normalise later.
    try:
        return str(mail_item.SenderEmailAddress or "")
    except Exception:
        return ""


def iter_pdf_file_attachments(msg) -> Iterable[tuple[int, object, str, int]]:
    """
    Yield tuples: (attachment_index, attachment_obj, file_name, size_bytes)
    Filters:
      - Attachment.Type == 1 (real file attachment)
      - filename ends with .pdf
    """
    att_count = int(getattr(msg.Attachments, "Count", 0))
    for j in range(1, att_count + 1):
        att = msg.Attachments.Item(j)
        att_type = int(getattr(att, "Type", 0))
        name = str(getattr(att, "FileName", "") or "")
        size = int(getattr(att, "Size", 0))

        if att_type != 1:
            continue
        if not name.lower().endswith(".pdf"):
            continue

        yield j, att, name, size


def save_and_hash_pdf(entry_id: str, attachment_index: int, att_obj, file_name: str) -> PdfAttachment:
    entry_short = short_entry_id(entry_id)
    safe_name = safe_filename(file_name)

    save_name = f"{entry_short}_{attachment_index:02d}_{safe_name}"
    save_path = STAGING_DIR / save_name

    att_obj.SaveAsFile(str(save_path))
    doc_hash = sha256_file(save_path)

    return PdfAttachment(
        attachment_index=attachment_index,
        file_name=file_name,
        size_bytes=save_path.stat().st_size,
        save_path=save_path,
        document_hash=doc_hash,
    )


# ----------------------------
# DB helpers (Block 4)
# ----------------------------

def begin_scan(conn, scan_ts: str) -> None:
    """
    Start-of-scan presence reset.
    Deterministic: anything not seen this scan remains 0.
    """
    cur = conn.cursor()
    cur.execute("UPDATE inbox_message SET is_currently_present = 0")
    cur.execute("UPDATE inbox_invoice SET is_currently_present = 0")
    # No commit here; caller controls transaction.


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
            scan_ts,  # first_seen_datetime on insert
            scan_ts,  # last_seen_datetime
            scan_ts,  # last_scan_datetime
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
    """
    Only Block 4 fields (presence + linkage + timestamps).
    PO/supplier/amounts/review fields are left NULL/default for now.
    """
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
            scan_ts,  # first_seen on insert
            scan_ts,  # last_seen
            scan_ts,  # last_scan
            source_folder_path,
        ),
    )

def scan_outlook_folder_to_db() -> dict:
    initialise_database()

    ns = get_outlook_namespace()
    store = get_mailbox(ns, MAILBOX_NAME)
    folder = get_folder_by_path(store, FOLDER_PATH)

    current_location = FOLDER_PATH
    scan_ts = now_iso_utc()

    messages_seen = 0
    pdfs_saved = 0

    conn = get_connection()
    try:
        conn.execute("BEGIN")
        begin_scan(conn, scan_ts)

        for msg in iter_latest_messages(folder, MAX_ITEMS):
            messages_seen += 1

            entry_id = str(msg.EntryID)
            received = str(getattr(msg, "ReceivedTime", "") or "")
            sender = safe_sender_email(msg)
            subject = str(getattr(msg, "Subject", "") or "")
            attachment_count = int(getattr(msg.Attachments, "Count", 0))

            pdf_count_for_message = 0

            # upsert message (set attachment_count to PDF count, not raw attachments)
            # so we’ll calculate pdf_count first
            for j, att, name, size in iter_pdf_file_attachments(msg):
                pdf_count_for_message += 1

                pdf = save_and_hash_pdf(entry_id, j, att, name)
                pdfs_saved += 1

                upsert_invoice(
                    conn,
                    document_hash=pdf.document_hash,
                    message_id=entry_id,
                    attachment_file_name=name,
                    scan_ts=scan_ts,
                    source_folder_path=current_location,
                )

            upsert_message(
                conn,
                message_id=entry_id,
                current_location=current_location,
                scan_ts=scan_ts,
                received_datetime=received or None,
                sender_address=sender or None,
                subject=subject or None,
                has_attachments=attachment_count > 0,
                attachment_count=pdf_count_for_message,  # IMPORTANT: PDFs only
            )

        conn.commit()

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "messages_seen": messages_seen,
        "pdfs_saved": pdfs_saved,
        "staging_dir": str(STAGING_DIR),
        "scan_ts": scan_ts,
        "mailbox": MAILBOX_NAME,
        "folder": FOLDER_PATH,
    }




if __name__ == "__main__":
    scan_outlook_folder_to_db()
