from __future__ import annotations
from pathlib import Path

from value_extraction import run_value_extraction
from po_validation import run_po_validation
from po_detection import run_po_detection
from db import initialise_database, get_connection
from outlook_scanner import scan_outlook_folder_to_db
from load_po_master import load_po_master

STAGING_DIR = Path(__file__).resolve().parent / "staging"


def print_tables() -> None:
    conn = get_connection()
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
    )
    tables = [row["name"] for row in cur.fetchall()]
    conn.close()
    print("DB tables:", tables)


def main() -> None:
    # 1) Ensure schema exists
    initialise_database()
    print_tables()

    # 2) Insert current PO Master
    po_master_summary = load_po_master(Path("data/Purchase_orders.csv"))
    print("\nPO Master Load:")
    print(po_master_summary)

    # 3) Scan Outlook + persist presence + hashes
    result = scan_outlook_folder_to_db()
    print()
    print("Scan summary:")
    print("Messages seen:", result["messages_seen"])
    print("PDF invoices saved:", result["pdfs_saved"])
    print("Staging folder:", result["staging_dir"])

    # 4) PO Detection from staging PDFs
    po_summary = run_po_detection(staging_dir=STAGING_DIR)
    print("\nPO Detection Results:")
    print(po_summary)

    # 5) PO Validation against po_master
    validation_summary = run_po_validation()
    print("\nPO Validation Results:")
    print(validation_summary)

    # 6) Value Extraction
    value_summary = run_value_extraction(staging_dir=STAGING_DIR)
    print("\nValue Extraction Results:")
    print(value_summary)


if __name__ == "__main__":
    main()


