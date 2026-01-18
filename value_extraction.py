from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, List

from db import get_connection
from fingerprint import sha256_file
from po_detection import index_staging_pdfs  # reuse your deterministic hash->path index


# ----------------------------
# Money parsing helpers
# ----------------------------

_MONEY_RE = r"([0-9][0-9,]*)(?:\.(\d{1,2}))?"


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
    # m.group(1) is full number part with commas, m.group(2) is decimals if present
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

# Rule B: Single total line e.g. "Total £16,618.44"
SINGLE_TOTAL_RE = re.compile(r"\bTOTAL\s*£\s*" + _MONEY_RE + r"\b", re.IGNORECASE)


def extract_values(text: str) -> ValueResult:
    """
    Deterministic V1 extraction:
    A) If Net Amount + VAT Amount found, take Total Amount if present else Due Amount.
    B) Else if "Total £X" found, set gross only.
    C) Else return all None.
    """
    if not text or not text.strip():
        return ValueResult(None, None, None, "NO_TEXT")

    net = _first_match_pence(NET_AMOUNT_RE, text)
    vat = _first_match_pence(VAT_AMOUNT_RE, text)

    if net is not None or vat is not None:
        gross = _first_match_pence(TOTAL_AMOUNT_RE, text)
        if gross is None:
            gross = _first_match_pence(DUE_AMOUNT_RE, text)

        # If we found net/vat but not gross, still write what we have.
        return ValueResult(net, vat, gross, "EXPLICIT_NET_VAT_BLOCK")

    gross_only = _first_match_pence(SINGLE_TOTAL_RE, text)
    if gross_only is not None:
        return ValueResult(None, None, gross_only, "SINGLE_TOTAL_LINE")

    return ValueResult(None, None, None, "NOT_FOUND")


# ----------------------------
# DB writeback
# ----------------------------

def write_value_results(conn, *, document_hash: str, result: ValueResult) -> None:
    cur = conn.cursor()
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
            ORDER BY document_hash ASC
            """
        ).fetchall()

        # Import your existing extractor (keeps one source of truth)
        from po_detection import extract_text_from_pdf  # uses pdfplumber deterministically

        for r in rows:
            document_hash = r["document_hash"]
            pdf_path = hash_to_path.get(document_hash)

            if not pdf_path:
                missing_file += 1
                continue

            text = extract_text_from_pdf(pdf_path)
            result = extract_values(text)
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
