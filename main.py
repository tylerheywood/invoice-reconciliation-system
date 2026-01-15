from pathlib import Path

from pdf_reader import pdf_to_text
from invoice_parser import extract_po_numbers
from db import get_connection, initialise_database
from fingerprint import sha256_file

BASE = Path(__file__).resolve().parent
INVOICES = BASE / "invoices"


def print_tables() -> None:
    conn = get_connection()
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables = [row["name"] for row in cur.fetchall()]
    conn.close()
    print("DB tables:", tables)


# Initialise DB (schema only)
initialise_database()
print_tables()

# Empty folder check
pdfs = list(INVOICES.glob("*.pdf"))
if not pdfs:
    print("No invoices found")

for pdf_path in INVOICES.glob("*.pdf"):
    print("\n----------------------------")
    print("Invoice:", pdf_path.name)

    doc_hash = sha256_file(pdf_path)
    print("Document hash:", doc_hash[:12])

    text = pdf_to_text(pdf_path)
    print("Characters extracted:", len(text))

    # "image-only PDF" handling
    if len(text.strip()) == 0:
        print("Match status: MANUAL_REVIEW_REQUIRED (NO_TEXT_LAYER)")
        continue

    pos = extract_po_numbers(text)
    count = len(pos)

    if count == 0:
        print("No PO Found")
    elif count == 1:
        print("PO Found:", next(iter(pos)))
    else:
        print(f"Multiple POs Found: {count}")
        print("POs:", pos)

conn = get_connection()
rows = conn.execute("PRAGMA table_info(inbox_invoice);").fetchall()
print([r[1] for r in rows])
conn.close()
