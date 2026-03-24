from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pdfplumber  # pip install pdfplumber

from .db import get_connection
from .fingerprint import sha256_file

"""
PO detection for the IRS pipeline.

Extracts text from staged PDFs, detects PO numbers via regex patterns,
and writes classification results back to the database.
"""

DEBUG = False

_ENV_DEBUG = os.getenv("ICS_DEBUG", "").strip().lower()
if _ENV_DEBUG in ("1", "true", "yes", "y", "on"):
    DEBUG = True

DEBUG_PREVIEW_MAX_LINES = 10
DEBUG_PREVIEW_MAX_CHARS_PER_LINE = 20


def _debug(msg: str) -> None:
    if DEBUG:
        print(msg)


def _clip_line(s: str, max_chars: int) -> str:
    s = s.rstrip("\n")
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "…"


def _debug_preview_text(text: str) -> None:
    if not DEBUG:
        return

    if not text or not text.strip():
        _debug("[PO] NO TEXT EXTRACTED (blank). Likely NO_TEXT_LAYER / scanned PDF.")
        return

    _debug(f"[PO] First {DEBUG_PREVIEW_MAX_LINES} lines (clipped to {DEBUG_PREVIEW_MAX_CHARS_PER_LINE} chars):")
    for line in text.splitlines()[:DEBUG_PREVIEW_MAX_LINES]:
        _debug(_clip_line(line, DEBUG_PREVIEW_MAX_CHARS_PER_LINE))


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
    """A single PO detection pattern: regex, normalizer, and optional overlap guard."""
    regex: re.Pattern
    normalizer: Callable[[re.Match], str]
    allow: Optional[Callable[[str, re.Match], bool]] = None


_DASH_CHARS = r"\-\u2010-\u2015"
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "po_patterns.json"

# Loaded from config; defaults used if config missing
_PO_PREFIX = "QAHE-PO-"
_PO_DIGIT_LENGTH = 6


def _load_config() -> dict | None:
    """Load PO pattern config from JSON file. Returns None if not found."""
    if _CONFIG_PATH.exists():
        try:
            with _CONFIG_PATH.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _init_patterns() -> tuple[str, int, List[PoPattern]]:
    """Build pattern list from config file, falling back to hardcoded defaults."""
    cfg = _load_config()

    prefix = cfg.get("prefix", "QAHE-PO-") if cfg else "QAHE-PO-"
    digit_length = cfg.get("digit_length", 6) if cfg else 6
    po_digits = rf"([0-9]{{{digit_length}}})"

    def normalizer(digits: str) -> str:
        cleaned = re.sub(r"\D+", "", digits)
        if len(cleaned) != digit_length:
            raise ValueError(f"Invalid PO digits: {digits!r}")
        return f"{prefix}{cleaned}"

    def allow_bare_po_match(text: str, match: re.Match) -> bool:
        """Prevent double-matching inside a full-prefix PO string."""
        start = match.start()
        if start == 0:
            return True
        window = text[max(0, start - 16) : start]
        collapsed = re.sub(rf"[\s{_DASH_CHARS}]+", "", window).upper()
        prefix_stem = prefix.rstrip("-").replace("-", "")
        return not collapsed.endswith(prefix_stem)

    if cfg and "patterns" in cfg:
        patterns = []
        for p in cfg["patterns"]:
            flags = re.IGNORECASE if p.get("case_insensitive") else 0
            allow = allow_bare_po_match if p.get("guard") == "no_prefix_overlap" else None
            patterns.append(PoPattern(
                regex=re.compile(p["regex"], flags),
                normalizer=lambda m, _n=normalizer: _n(m.group(1)),
                allow=allow,
            ))
        return prefix, digit_length, patterns

    # Hardcoded fallback
    return prefix, digit_length, [
        PoPattern(
            re.compile(rf"\bQAHE\s*[{_DASH_CHARS}]\s*PO\s*[{_DASH_CHARS}]\s*{po_digits}\b", re.IGNORECASE),
            lambda m, _n=normalizer: _n(m.group(1)),
        ),
        PoPattern(
            re.compile(rf"\bPurchase\s*Order\s*[:#]?\s*(?:PO\s*[{_DASH_CHARS}]\s*)?{po_digits}\b", re.IGNORECASE),
            lambda m, _n=normalizer: _n(m.group(1)),
        ),
        PoPattern(
            re.compile(rf"\bPO\s*#?\s*:\s*{po_digits}\b", re.IGNORECASE),
            lambda m, _n=normalizer: _n(m.group(1)),
        ),
        PoPattern(
            re.compile(rf"\bPO\s*[{_DASH_CHARS}]\s*{po_digits}\b", re.IGNORECASE),
            lambda m, _n=normalizer: _n(m.group(1)),
            allow=allow_bare_po_match,
        ),
    ]


_PO_PREFIX, _PO_DIGIT_LENGTH, PO_PATTERNS = _init_patterns()


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
                page_text = page_text.replace("\r\n", "\n").replace("\r", "\n")
                chunks.append(page_text)
        return "\n".join(chunks).strip()
    except Exception:
        return ""


def detect_po_numbers(text: str) -> List[str]:
    """Extract PO numbers from text. Returns unique canonical values in first-seen order."""
    if not text:
        return []

    seen: set[str] = set()
    ordered: List[str] = []

    for pattern in PO_PATTERNS:
        for match in pattern.regex.finditer(text):
            if pattern.allow is not None and not pattern.allow(text, match):
                continue

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
    """
    if not text or not text.strip():
        return PoDetectionResult([], 0, NO_TEXT_LAYER)

    if not po_numbers:
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
    If duplicates exist (same hash saved multiple times), keep first by sorted path order.
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
        UPDATE invoice_document
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
    no_text = 0

    try:
        conn.execute("BEGIN")

        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT document_hash
            FROM invoice_document
            WHERE is_currently_present = 1
              AND (po_match_status IS NULL OR po_match_status = 'UNSCANNED')
            ORDER BY document_hash ASC
            """
        ).fetchall()

        _debug(f"[PO] Candidate invoices needing detection: {len(rows)}")
        _debug(f"[PO] Staging index size: {len(hash_to_path)}")

        for r in rows:
            document_hash = r["document_hash"]
            pdf_path = hash_to_path.get(document_hash)

            _debug(f"[PO] Processing {document_hash} (pdf_found={bool(pdf_path)})")

            if not pdf_path:
                write_po_results(
                    conn,
                    document_hash=document_hash,
                    result=PoDetectionResult([], 0, FILE_MISSING),
                )
                missing_file += 1
                continue

            text = extract_text_from_pdf(pdf_path)
            _debug(f"[PO] Extracted text length: {len(text) if text else 0}")
            _debug_preview_text(text)

            po_numbers = detect_po_numbers(text)
            result = classify_po_result(text, po_numbers)

            _debug(f"[PO] Result: {result.match_status} | po_count={result.po_count} | pos={result.po_numbers}")

            if result.match_status == NO_TEXT_LAYER:
                no_text += 1

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
        "no_text_layer": no_text,
        "staging_index_size": len(hash_to_path),
    }
