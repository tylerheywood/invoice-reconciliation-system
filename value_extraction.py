from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from db import get_connection
from po_detection import index_staging_pdfs  # reuse your deterministic hash->path index


# =============================================================================
# Value Extraction (V1)
#
# DEBUG mode:
#   - Toggle DEBUG below, or set env var ICS_DEBUG=1
#   - Logs are truncated to avoid megaspam
# =============================================================================

# Toggle here for local runs
DEBUG = False

# Optional: allow env override (ICS_DEBUG=1 / true / yes)
_ENV_DEBUG = os.getenv("ICS_DEBUG", "").strip().lower()
if _ENV_DEBUG in ("1", "true", "yes", "y", "on"):
    DEBUG = True

# Debug display controls
DEBUG_PREVIEW_MAX_LINES = 40
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
    """
    Prints a small, safe preview of extracted text:
    - first N lines
    - each line clipped to M chars
    """
    if not DEBUG:
        return

    if not text or not text.strip():
        _debug("[VALUE] NO TEXT EXTRACTED (blank). Likely NO_TEXT_LAYER / scanned PDF.")
        return

    _debug(f"[VALUE] First {DEBUG_PREVIEW_MAX_LINES} lines (clipped to {DEBUG_PREVIEW_MAX_CHARS_PER_LINE} chars):")
    for line in text.splitlines()[:DEBUG_PREVIEW_MAX_LINES]:
        _debug(_clip_line(line, DEBUG_PREVIEW_MAX_CHARS_PER_LINE))


# ----------------------------
# Money parsing helpers
# ----------------------------

# General money (net/vat/total amount blocks) can be looser
_MONEY_RE = r"([0-9][0-9,]*)(?:\.(\d{1,2}))?"

# Totals must be stricter to avoid capturing PO-like integers.
# REQUIRE decimals (e.g. 1234.56). £ symbol is optional.
_TOTAL_MONEY_RE = r"(?:£\s*)?([0-9][0-9,]*)\.(\d{1,2})"


def _money_to_pence(amount_str: str) -> int:
    """
    Convert '1,796.25' -> 179625
    Convert '1013.25'  -> 101325
    Convert '16,618.44'-> 1661844
    """
    s = amount_str.strip().replace("£", "").replace(",", "")
    if not s:
        raise ValueError("Blank amount")

    if "." in s:
        pounds, pence = s.split(".", 1)
        pence = (pence + "00")[:2]
    else:
        pounds, pence = s, "00"

    return int(pounds) * 100 + int(pence)


def _first_match_pence(pattern: re.Pattern[str], text: str) -> Optional[int]:
    m = pattern.search(text)
    if not m:
        return None
    whole = m.group(1)
    dec = m.group(2) or ""
    num = f"{whole}.{dec}" if dec else whole
    return _money_to_pence(num)


# ----------------------------
# Extraction rules (V1)
# ----------------------------

@dataclass(frozen=True)
class ValueResult:
    net_pence: Optional[int]
    vat_pence: Optional[int]
    gross_pence: Optional[int]
    rule: str  # which deterministic rule fired


# Rule A: Explicit block e.g. "Net Amount : £1,496.87"
NET_AMOUNT_RE = re.compile(r"\bNET\s+AMOUNT\s*[:\-]?\s*£?\s*" + _MONEY_RE, re.IGNORECASE)
VAT_AMOUNT_RE = re.compile(r"\bVAT\s+AMOUNT\s*[:\-]?\s*£?\s*" + _MONEY_RE, re.IGNORECASE)
TOTAL_AMOUNT_RE = re.compile(r"\bTOTAL\s+AMOUNT\s*[:\-]?\s*£?\s*" + _MONEY_RE, re.IGNORECASE)
DUE_AMOUNT_RE = re.compile(r"\bDUE\s+AMOUNT\s*[:\-]?\s*£?\s*" + _MONEY_RE, re.IGNORECASE)

# Rule B: Single total line (STRICT: must include decimals)
SINGLE_TOTAL_RE = re.compile(r"\bTOTAL\b\s*[:\-]?\s*" + _TOTAL_MONEY_RE + r"\b", re.IGNORECASE)

# Rule C: Common labelled totals (STRICT: must include decimals)
LABELED_TOTAL_RE = re.compile(
    r"\b(?:INVOICE\s+TOTAL|TOTAL\s+DUE|AMOUNT\s+DUE|BALANCE\s+DUE|GRAND\s+TOTAL|TOTAL\s+PAYABLE|TOTAL\s+TO\s+PAY)\b"
    r"\s*[:\-]?\s*"
    + _TOTAL_MONEY_RE
    + r"\b",
    re.IGNORECASE,
)


def extract_values(text: str) -> ValueResult:
    """
    Deterministic V1 extraction:
    A) If Net Amount and/or VAT Amount found, take Total Amount if present else Due Amount.
    B) Else if "Total ..." found (strict, decimals required), set gross only.
    C) Else if labelled total found (strict, decimals required), set gross only.
    D) Else return all None.
    """
    if not text or not text.strip():
        return ValueResult(None, None, None, "NO_TEXT")

    net = _first_match_pence(NET_AMOUNT_RE, text)
    vat = _first_match_pence(VAT_AMOUNT_RE, text)

    if net is not None or vat is not None:
        gross = _first_match_pence(TOTAL_AMOUNT_RE, text)
        if gross is None:
            gross = _first_match_pence(DUE_AMOUNT_RE, text)
        return ValueResult(net, vat, gross, "EXPLICIT_NET_VAT_BLOCK")

    gross_only = _first_match_pence(SINGLE_TOTAL_RE, text)
    if gross_only is not None:
        return ValueResult(None, None, gross_only, "SINGLE_TOTAL_LINE")

    gross_labeled = _first_match_pence(LABELED_TOTAL_RE, text)
    if gross_labeled is not None:
        return ValueResult(None, None, gross_labeled, "LABELED_TOTAL")

    return ValueResult(None, None, None, "NOT_FOUND")


# ----------------------------
# DB writeback
# ----------------------------

def write_value_results(conn, *, document_hash: str, result: ValueResult) -> None:
    cur = conn.cursor()

    # If no text at all, flag for manual lane and stop re-processing forever.
    if result.rule == "NO_TEXT":
        cur.execute(
            """
            UPDATE inbox_invoice
            SET po_match_status = 'NO_TEXT_LAYER'
            WHERE document_hash = ?
            """,
            (document_hash,),
        )
        return

    cur.execute(
        """
        UPDATE inbox_invoice
        SET net_total = ?,
            vat_total = ?,
            gross_total = ?
        WHERE document_hash = ?
        """,
        (result.net_pence, result.vat_pence, result.gross_pence, document_hash),
    )


# ----------------------------
# Runner (staging -> DB)
# ----------------------------

def run_value_extraction(*, staging_dir: Path) -> dict:
    """
    - Build hash->path index from staging
    - For present invoices, extract/write values
    - Only process invoices where gross_total IS NULL (or 0) to stay idempotent
    - Skip NO_TEXT_LAYER so scanned PDFs don't get hammered every run
    """
    hash_to_path = index_staging_pdfs(staging_dir)

    conn = get_connection()
    processed = 0
    missing_file = 0
    values_found = 0

    try:
        conn.execute("BEGIN")
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT document_hash
            FROM inbox_invoice
            WHERE is_currently_present = 1
              AND (gross_total IS NULL OR gross_total = 0)
              AND (po_match_status IS NULL OR po_match_status <> 'NO_TEXT_LAYER')
            ORDER BY document_hash ASC
            """
        ).fetchall()

        _debug(f"[VALUE] Candidate invoices needing extraction: {len(rows)}")
        _debug(f"[VALUE] Staging index size: {len(hash_to_path)}")

        # Import your existing extractor (keeps one source of truth)
        from po_detection import extract_text_from_pdf  # uses pdfplumber deterministically

        for r in rows:
            document_hash = r["document_hash"]
            pdf_path = hash_to_path.get(document_hash)

            _debug(f"[VALUE] Processing {document_hash} (pdf_found={bool(pdf_path)})")

            if not pdf_path:
                missing_file += 1
                continue

            text = extract_text_from_pdf(pdf_path)

            _debug(f"[VALUE] Extracted text length: {len(text) if text else 0}")
            _debug_preview_text(text)

            result = extract_values(text)

            _debug(
                f"[VALUE] Rule fired: {result.rule} | gross_pence={result.gross_pence} | "
                f"net={result.net_pence} | vat={result.vat_pence}"
            )

            write_value_results(conn, document_hash=document_hash, result=result)

            processed += 1
            if result.gross_pence is not None:
                values_found += 1

        conn.commit()

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "processed": processed,
        "values_found_gross": values_found,
        "missing_file": missing_file,
        "staging_index_size": len(hash_to_path),
    }


if __name__ == "__main__":
    # Tiny self-test harness (kept)
    samples = [
        "Invoice total: 123456",
        "Invoice total: 123456.00",
        "Invoice total: £123.45",
        "Total: 99.99",
        "Total £16,618.44",
        "Net amount: £100.00\nVAT amount: £20.00\nTotal amount: £120.00",
    ]
    for s in samples:
        r = extract_values(s)
        print(s.replace("\n", " | "), "=>", r)
