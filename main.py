'''
- need to add document_hash generator
- need to make sure tables are set-up correctly
- link up with outlook
'''
from pathlib import Path
from pdf_reader import pdf_to_text
from invoice_parser import extract_po_numbers
from db import get_connection, initialise_database

BASE = Path(__file__).resolve().parent
INVOICES = BASE / "invoices"

get_connection()
initialise_database()

# Empty folder check

pdfs = list(INVOICES.glob("*.pdf"))
if not pdfs:
    print("No invoices found")

# Load all PDF files in the 'invoices' folder, extracts the PO numbers and returns if valid PO number found or not

for pdf_path in INVOICES.glob("*.pdf"):
    print("\n----------------------------")
    print("Invoice:", pdf_path.name)

    text = pdf_to_text(pdf_path)
    print("Characters extracted:", len(text))

    pos = extract_po_numbers(text)
    count = len(pos)

    if count == 0:
        print("No PO Found")
    elif count == 1:
        print("PO Found:", next(iter(pos)))
    else:
        print(f"Multiple POs Found: {count}")
        print("POs:", pos)
