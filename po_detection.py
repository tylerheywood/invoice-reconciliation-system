from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

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
FILE_MISSING = "FILE_MISSING"


# ----------------------------
# PO detection rules
# ----------------------------

@dataclass(frozen=True)
class PoDetectionResult:
    po_numbers: List[str]  # unique, deterministic order
    po_count: int
    match_status: str


@dataclass(frozen=True)
class PoPattern:
    """
    A single PO detection variant.

    - regex: finds a PO token
    - normalizer: returns canonical PO string (QAHE-PO-XXXXXX)
    - allow: optional predicate to suppress overlaps/false positives
    """
    regex: re.Pattern
    normalizer: Callable[[re.Match], str]
    allow: Optional[Callable[[str, re.Match], bool]] = None


# Treat hyphen-like unicode dashes as hyphens in patterns.
_DASH_CHARS = r"\-\u2010-\u2015"  # -, ‐-‒–—―

# Canonical token: strictly 6 digits everywhere
PO_DIGITS = r"([0-9]{6})"


def normalize_qahe_po_digits(digits: str) -> str:
    """
    Canonical PO format as per po_master: QAHE-PO-XXXXXX
    """
    # Defensive: keep only digits and enforce 6 chars if someone passes junk.
    cleaned = re.sub(r"\D+", "", digits)
    if len(cleaned) != 6:
        # Don't silently invent a PO; callers should only pass valid 6 digits.
        raise ValueError(f"Invalid PO digits: {digits!r}")
    return f"QAHE-PO-{cleaned}"


def allow_bare_po_match(text: str, match: re.Match) -> bool:
    """
    Prevent the bare PO matcher ("PO-123456") from also matching inside an explicit
    QAHE PO like "QAHE - PO - 123456".

    We only guard the *bare* PO pattern. Labelled patterns ("Purchase order:", "PO:")
    are higher signal and remain independent.

    Strategy:
      - Look at a small window immediately before match start
      - Collapse whitespace and dash-like characters
      - If it ends with 'QAHE', then this bare match is part of 'QAHE - PO - ...'
    """
    start = match.start()
    if start == 0:
        return True

    window = text[max(0, start - 16) : start]
    collapsed = re.sub(rf"[\s{_DASH_CHARS}]+", "", window).upper()
    return not collapsed.endswith("QAHE")


# Add patterns here to extend PO detection variants.
# Keep patterns small and explicit; add more PoPattern entries rather than one mega-regex.
PO_PATTERNS: List[PoPattern] = [
    # Highest-signal org-specific:
    #   QAHE - PO - 123456
    #   QAHE-PO-123456
    PoPattern(
        re.compile(
            rf"\bQAHE\s*[{_DASH_CHARS}]\s*PO\s*[{_DASH_CHARS}]\s*{PO_DIGITS}\b",
            re.IGNORECASE,
        ),
        lambda m: normalize_qahe_po_digits(m.group(1)),
    ),

    # "Purchase order: 123456"
    # "Purchase order: PO-123456"
    # "Purchase order # 123456"
    PoPattern(
        re.compile(
            rf"\bPurchase\s*Order\s*[:#]?\s*(?:PO\s*[{_DASH_CHARS}]\s*)?{PO_DIGITS}\b",
            re.IGNORECASE,
        ),
        lambda m: normalize_qahe_po_digits(m.group(1)),
    ),

    # "PO: 123456"
    # "PO #: 123456"
    PoPattern(
        re.compile(rf"\bPO\s*#?\s*:\s*{PO_DIGITS}\b", re.IGNORECASE),
        lambda m: normalize_qahe_po_digits(m.group(1)),
    ),

    # "PO-123456" / "PO - 123456" (anywhere in text)
    # Guarded to avoid duplicating the QAHE pattern.
    PoPattern(
        re.compile(rf"\bPO\s*[{_DASH_CHARS}]\s*{PO_DIGITS}\b", re.IGNORECASE),
        lambda m: normalize_qahe_po_digits(m.group(1)),
        allow=allow_bare_po_match,
    ),
]


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
        return "\n".join(chunks).strip()
    except Exception:
        # Treat any read/extract failure as NO_TEXT_LAYER for this block
        return ""


def detect_po_numbers(text: str) -> List[str]:
    """
    Deterministic PO extraction.
    - Finds matches with PO_PATTERNS
    - Normalises to canonical form (QAHE-PO-XXXXXX)
    - Returns unique values in first-seen order
    """
    if not text:
        return []

    seen: set[str] = set()
    ordered: List[str] = []

    for pattern in PO_PATTERNS:
        for match in pattern.regex.finditer(text):
            if pattern.allow is not None and not pattern.allow(text, match):
                continue

            # Normalizer returns canonical QAHE-PO-XXXXXX; if it raises, skip the match.
            try:
                po = pattern.normalizer(match)
            except ValueError:
                continue

            if po not in seen:
                seen.add(po)
                ordered.append(po)

    return ordered


def classify_po_result(text: str, po_numbers: List[str]) -> PoDetectionResult:
    """
    Detection-only classification.

    This function answers ONE question:
    "What did we detect from the document text?"

    It does NOT attempt to validate POs against po_master.
    Validation happens in a later pipeline stage.
    """
    # No usable text layer at all
    if not text or not text.strip():
        return PoDetectionResult(
            po_numbers=[],
            po_count=0,
            match_status=NO_TEXT_LAYER,
        )

    # Text present, but no PO-like tokens detected
    if not po_numbers:
        return PoDetectionResult(
            po_numbers=[],
            po_count=0,
            match_status=MISSING_PO,
        )

    # More than one distinct PO detected
    if len(po_numbers) > 1:
        return PoDetectionResult(
            po_numbers=po_numbers,
            po_count=len(po_numbers),
            match_status=MULTIPLE_POS,
        )

    # Exactly one PO detected
    return PoDetectionResult(
        po_numbers=po_numbers,
        po_count=1,
        match_status=SINGLE_PO_DETECTED,
    )


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
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE inbox_invoice
        SET
            po_count = ?,
            po_match_status = ?,
            po_validation_status = 'UNVALIDATED'
        WHERE document_hash = ?
        """,
        (result.po_count, result.match_status, document_hash),
    )

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
                # DB says invoice exists, but staging doesn't have it.
                write_po_results(
                    conn,
                    document_hash=document_hash,
                    result=PoDetectionResult([], 0, FILE_MISSING),
                )
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
