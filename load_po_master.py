from pathlib import Path
import csv
from datetime import datetime, timezone

from db import get_connection


def load_po_master(csv_path: Path) -> dict:
    conn = get_connection()
    cur = conn.cursor()

    # Clear existing data (V1 replace semantics)
    cur.execute("DELETE FROM po_master")

    now = datetime.now(timezone.utc).isoformat()

    inserted = 0

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        field_map = {name.strip().lower(): name for name in reader.fieldnames}

        po_col = field_map.get("purchase order")
        supplier_col = field_map.get("supplier account")
        status_col = field_map.get("purchase order status")

        if not po_col or not supplier_col:
            raise ValueError(f"Unexpected CSV columns: {reader.fieldnames}")

        for row in reader:
            po_number = row[po_col].strip()
            supplier_account = row[supplier_col].strip()
            po_status = row[status_col].strip() if status_col else None

            if not po_number or not supplier_account:
                continue

            cur.execute(
                """
                INSERT INTO po_master (
                    po_number,
                    supplier_account,
                    po_status,
                    last_import_datetime
                ) VALUES (?, ?, ?, ?)
                """,
                (po_number, supplier_account, po_status, now),
            )

            inserted += cur.rowcount

    conn.commit()

    return {
        "po_loaded": inserted,
        "source_file": str(csv_path),
        "imported_at": now,
    }


if __name__ == "__main__":
    from pathlib import Path

    summary = load_po_master(Path("data/Purchase_orders.csv"))
    print(summary)
