from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import pdfplumber  # pip install pdfplumber

from db import get_connection
from fingerprint import sha256_file


# ----------------------------
# Status outcomes
# ----------------------------

NO_TEXT_LAYER = "NO_TEXT_LAYER"
MISSING_PO = "MISSING_PO"
MULTIPLE_POS = "MULTIPLE_POS"
SINGLE_PO_DETECTED = "SINGLE_PO_DETECTED"


# ----------------------------
# PO detection rules
# ----------------------------

@dataclass(frozen=True)
class PoDetectionResult:
    po_numbers: List[str]          # unique, deterministic order
    po_count: int
    match_status: str


# Adjust this pattern to match your organisation’s PO format.
# Keep it deterministic, simple, and explainable.
#
# Example: QA-HE followed by 6 digits
PO_PATTERN = re.compile(r"\bQA-HE-[\s\-:]*([0-9]{6})\b", re.IGNORECASE)


def extract_text_from_pdf(pdf_path: Path) -> str:
    """
    Deterministic text extraction.
    Returns a single concatenated string. If no usable text layer, returns "".
    """
    try:
        chunks: List[str] = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                # Normalise line endings to keep consistent parsing
                page_text = page_text.replace("\r\n", "\n").replace("\r", "\n")
                chunks.append(page_text)
        text = "\n".join(chunks).strip()
        return text
    except Exception:
        # For Block 5 initial set, treat any read/extract failure as NO_TEXT_LAYER
        return ""


def detect_po_numbers(text: str) -> List[str]:
    """
    Deterministic PO extraction.
    - Finds matches with PO_PATTERN
    - Normalises to canonical form (e.g. PO123456)
    - Returns unique values in first-seen order
    """
    if not text:
        return []

    seen = set()
    ordered: List[str] = []

    for m in PO_PATTERN.finditer(text):
        digits = m.group(1)
        po = f"PO{digits}"
        if po not in seen:
            seen.add(po)
            ordered.append(po)

    return ordered


def classify_po_result(text: str, po_numbers: List[str]) -> PoDetectionResult:
    """
    Apply Block 5 classification rules.
    """
    if not text or len(text.strip()) == 0:
        return PoDetectionResult([], 0, NO_TEXT_LAYER)

    if len(po_numbers) == 0:
        return PoDetectionResult([], 0, MISSING_PO)

    if len(po_numbers) > 1:
        return PoDetectionResult(po_numbers, len(po_numbers), MULTIPLE_POS)

    return PoDetectionResult(po_numbers, 1, SINGLE_PO_DETECTED)


# ----------------------------
# Staging index (hash -> file)
# ----------------------------

def index_staging_pdfs(staging_dir: Path) -> Dict[str, Path]:
    """
    Deterministically map document_hash -> staged PDF path.
    If duplicates exist (same hash saved multiple times), we keep the first
    encountered by sorted path order to stay deterministic.
    """
    pdf_paths = sorted(staging_dir.glob("*.pdf"))
    mapping: Dict[str, Path] = {}

    for p in pdf_paths:
        try:
            h = sha256_file(p)
        except Exception:
            continue

        if h not in mapping:
            mapping[h] = p

    return mapping


# ----------------------------
# DB writeback (latest truth)
# ----------------------------

def write_po_results(conn, *, document_hash: str, result: PoDetectionResult) -> None:
    """
    Update inbox_invoice + replace invoice_po rows for this invoice.
    """
    cur = conn.cursor()

    # Update invoice classification fields
    cur.execute(
        """
        UPDATE inbox_invoice
        SET po_count = ?, po_match_status = ?
        WHERE document_hash = ?
        """,
        (result.po_count, result.match_status, document_hash),
    )

    # Replace invoice_po rows (latest truth)
    cur.execute("DELETE FROM invoice_po WHERE document_hash = ?", (document_hash,))
    for po in result.po_numbers:
        cur.execute(
            """
            INSERT INTO invoice_po (document_hash, po_number)
            VALUES (?, ?)
            """,
            (document_hash, po),
        )


def run_po_detection(*, staging_dir: Path) -> dict:
    """
    Entry point:
    - Build hash->path index from staging
    - Fetch currently-present invoices from DB needing scan
    - Extract text, detect PO(s), classify, write back
    """
    hash_to_path = index_staging_pdfs(staging_dir)

    conn = get_connection()
    processed = 0
    missing_file = 0

    try:
        conn.execute("BEGIN")

        # Process invoices that are currently present AND not scanned yet.
        # (You set 'UNSCANNED' on insert, so this is deterministic and safe.)
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT document_hash
            FROM inbox_invoice
            WHERE is_currently_present = 1
              AND (po_match_status IS NULL OR po_match_status = 'UNSCANNED')
            ORDER BY document_hash ASC
            """
        ).fetchall()

        for r in rows:
            document_hash = r["document_hash"]

            pdf_path = hash_to_path.get(document_hash)
            if not pdf_path:
                # Control implication: DB says invoice exists, but staging doesn't have it.
                # For now, we just count and skip (do not invent a status).
                missing_file += 1
                continue

            text = extract_text_from_pdf(pdf_path)
            po_numbers = detect_po_numbers(text)
            result = classify_po_result(text, po_numbers)

            write_po_results(conn, document_hash=document_hash, result=result)
            processed += 1

        conn.commit()

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "processed": processed,
        "missing_file": missing_file,
        "staging_index_size": len(hash_to_path),
    }
