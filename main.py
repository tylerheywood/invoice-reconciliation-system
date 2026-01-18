from __future__ import annotations
from pathlib import Path

from value_extraction import run_value_extraction
from po_detection import run_po_detection
from db import initialise_database, get_connection
from outlook_scanner import scan_outlook_folder_to_db, STAGING_DIR  # we’ll add this function

STAGING_DIR = Path(__file__).resolve().parent / "staging"


def print_tables() -> None:
    conn = get_connection()
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables = [row["name"] for row in cur.fetchall()]
    conn.close()
    print("DB tables:", tables)


def main() -> None:
    # 1) Ensure schema exists
    initialise_database()
    print_tables()

    # 2) scan Outlook + persist presence + hashes
    result = scan_outlook_folder_to_db()
    print()
    print("Scan summary:")
    print("Messages seen:", result["messages_seen"])
    print("PDF invoices saved:", result["pdfs_saved"])
    print("Staging folder:", result["staging_dir"])

    # 3) PO Detection from staging PDFs
    po_summary = run_po_detection(staging_dir=STAGING_DIR)
    print("\nPO Detection Results:")
    print(po_summary)

    # 4) Value Extraction
    value_summary = run_value_extraction(staging_dir=STAGING_DIR)
    print("\nValue Extraction Results:")
    print(value_summary)


if __name__ == "__main__":
    main()
