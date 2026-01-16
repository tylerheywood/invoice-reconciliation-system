from __future__ import annotations

from db import initialise_database, get_connection
from outlook_scanner import scan_outlook_folder_to_db  # we’ll add this function


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


if __name__ == "__main__":
    main()
