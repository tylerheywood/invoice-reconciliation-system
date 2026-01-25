from __future__ import annotations

import os
from pathlib import Path

from value_extraction import run_value_extraction
from po_validation import run_po_validation
from po_detection import run_po_detection
from db import initialise_database, get_connection
from outlook_scanner import scan_outlook_folder_to_db
from load_po_master import load_po_master
from worklist import refresh_worklist_tables

STAGING_DIR = Path(__file__).resolve().parent / "staging"

# -----------------------------------------------------------------------------
# Debug toggle
#   - default DEBUG=False
#   - override with env var: ICS_DEBUG=1
# -----------------------------------------------------------------------------
DEBUG = False

_ENV_DEBUG = os.getenv("ICS_DEBUG", "").strip().lower()
if _ENV_DEBUG in ("1", "true", "yes", "y", "on"):
    DEBUG = True


def dprint(*args, **kwargs) -> None:
    """Debug-only print."""
    if DEBUG:
        print(*args, **kwargs)


def print_tables() -> None:
    conn = get_connection()
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables = [row["name"] for row in cur.fetchall()]
    conn.close()
    dprint("DB tables:", tables)


def main() -> None:
    print("=== AP Inbox Control — Pipeline Run ===")
    dprint("[DEBUG] Enabled via ICS_DEBUG=1")

    # 1) Ensure schema exists
    initialise_database()
    print_tables()

    # 2) Insert current PO Master
    print("\n--- Stage 1: PO Master Load ---")
    po_master_summary = load_po_master(Path("data/Purchase_orders.csv"))
    print(po_master_summary)

    # 3) Scan Outlook + persist presence + hashes
    print("\n--- Stage 2: Outlook Scan ---")
    result = scan_outlook_folder_to_db()
    print(
        {
            "messages_seen": result.get("messages_seen"),
            "pdfs_saved": result.get("pdfs_saved"),
            "staging_dir": result.get("staging_dir"),
        }
    )
    dprint("Full scan result:", result)

    # 4) PO Detection from staging PDFs
    print("\n--- Stage 3: PO Detection ---")
    po_summary = run_po_detection(staging_dir=STAGING_DIR)
    print(po_summary)

    # 5) PO Validation against po_master
    print("\n--- Stage 4: PO Validation ---")
    validation_summary = run_po_validation()
    print(validation_summary)

    # 6) Value Extraction
    print("\n--- Stage 5: Value Extraction ---")
    value_summary = run_value_extraction(staging_dir=STAGING_DIR)
    print(value_summary)

    print("\n=== Done ===")

    conn = get_connection()
    run_id = refresh_worklist_tables(conn)
    conn.close()
    print(f"Worklist refreshed. run_id={run_id}")


if __name__ == "__main__":
    main()
