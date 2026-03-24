from __future__ import annotations

'''
Invoice Reconciliation System — Pipeline Entry Point

Processes invoices from a local input folder through PO matching,
validation, and value extraction. Outputs a snapshot for the dashboard.
'''

import argparse
import time
import traceback
from datetime import datetime, timezone
import os
import sys
from pathlib import Path

DEBUG = False
_ENV_DEBUG = os.getenv("ICS_DEBUG", "").strip().lower()
if _ENV_DEBUG in ("1", "true", "yes", "y", "on"):
    DEBUG = True

def dprint(*args, **kwargs) -> None:
    if DEBUG:
        print(*args, **kwargs)

from core.folder_scanner import scan_folder_to_db
from core.po_validation import run_po_validation
from core.po_detection import run_po_detection
from core.db import initialise_database, get_connection
from core.load_po_master import load_po_master
from core.worklist import refresh_worklist_tables
from core.value_extraction import run_value_extraction
from core.duplicate_detection import run_duplicate_detection
from core.notifications import notify_new_exceptions
from core.worklist import fetch_current_worklist
from core.snapshot import write_snapshot


STAGING_DIR = Path(__file__).resolve().parent / "staging"
INPUT_DIR = Path(os.getenv("ICS_INPUT_DIR", str(Path(__file__).resolve().parent / "input")))
INPUT_DIR.mkdir(exist_ok=True)


def print_tables() -> None:
    conn = get_connection()
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables = [row["name"] for row in cur.fetchall()]
    conn.close()
    dprint("DB tables:", tables)


def main() -> None:
    # Guard against stale database from a previous schema version
    old_db = Path(__file__).resolve().parent / "inbox.db"
    if old_db.exists():
        print("[WARNING] inbox.db detected from a previous version. The schema has changed.")
        print("Delete inbox.db and re-run the pipeline to initialise the new schema.")
        print("Exiting.")
        sys.exit(1)

    print(f"[BOOT] {datetime.now(timezone.utc).isoformat()} main starting", flush=True)
    print("=== Invoice Reconciliation System — Pipeline Run ===")
    dprint("[DEBUG] Enabled via ICS_DEBUG=1")

    # Ensure schema exists
    initialise_database()
    print_tables()

    # Stage 1: PO Master
    print("\n--- Stage 1: PO Master Load ---")
    data_dir = Path(__file__).resolve().parent / "data"
    po_upload_candidates = sorted(data_dir.glob("po_upload.*")) if data_dir.is_dir() else []
    default_po_path = data_dir / "Purchase_orders.csv"

    if po_upload_candidates:
        po_file = po_upload_candidates[0]
    elif default_po_path.exists():
        po_file = default_po_path
    else:
        po_file = None

    if po_file:
        po_master_summary = load_po_master(po_file)
        print(po_master_summary)
    else:
        print("[WARN] No PO master file found in data/. Skipping PO Master Load.")
        print("       Upload one via the dashboard or place Purchase_orders.csv in data/")

    # Stage 2: Folder scan
    print("\n--- Stage 2: Folder Scan ---", flush=True)
    print(f"[SCAN] scanning {INPUT_DIR} ...", flush=True)
    result = scan_folder_to_db(input_dir=INPUT_DIR)
    print("[SCAN] completed folder scan", flush=True)
    print(
        {
            "messages_seen": result.get("messages_seen"),
            "pdfs_saved": result.get("pdfs_saved"),
            "staging_dir": result.get("staging_dir"),
        }
    )
    dprint("Full scan result:", result)

    # Stage 3: PO Detection
    print("\n--- Stage 3: PO Detection ---")
    po_summary = run_po_detection(staging_dir=STAGING_DIR)
    print(po_summary)

    # Stage 4: PO Validation
    print("\n--- Stage 4: PO Validation ---")
    validation_summary = run_po_validation()
    print(validation_summary)

    # Stage 5: Value Extraction
    print("\n--- Stage 5: Value Extraction ---")
    value_summary = run_value_extraction(staging_dir=STAGING_DIR)
    print(value_summary)

    # Stage 6: Duplicate Detection
    print("\n--- Stage 6: Duplicate Detection ---")
    dup_summary = run_duplicate_detection()
    print(dup_summary)

    print("\n=== Done ===")

    # Capture previous worklist hashes for webhook diff
    conn = get_connection()
    try:
        prev_items = fetch_current_worklist(conn)
        prev_hashes = {item["document_hash"] for item in prev_items if item.get("next_action") == "MANUAL REVIEW"}
        run_id = refresh_worklist_tables(conn)
        current_items = fetch_current_worklist(conn)
    finally:
        conn.close()
    print(f"Worklist refreshed. run_id={run_id}")

    # Notify webhook of new exceptions
    notified = notify_new_exceptions(current_items, prev_hashes)
    if notified:
        print(f"[WEBHOOK] Notified {notified} new exception(s)")

    # Stage 7: Dashboard snapshot
    out = write_snapshot()
    print(f"[SNAPSHOT] Written to {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IRS Pipeline")
    parser.add_argument("--watch", action="store_true", help="Re-run pipeline on an interval")
    parser.add_argument("--interval", type=int, default=300, help="Watch interval in seconds (default: 300)")
    args = parser.parse_args()

    if args.watch:
        print(f"[WATCH] Running pipeline every {args.interval}s. Ctrl+C to stop.")
        while True:
            try:
                main()
            except Exception as e:
                print(f"[WATCH] Pipeline error: {e}", flush=True)
                traceback.print_exc()
            print(f"[WATCH] Next run in {args.interval}s...", flush=True)
            time.sleep(args.interval)
    else:
        main()
