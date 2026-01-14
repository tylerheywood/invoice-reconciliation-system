from pathlib import Path
from pdf_reader import pdf_to_text

BASE = Path(__file__).resolve().parent
pdf_path = BASE / "invoices" / "invoice1.pdf"

text = pdf_to_text(pdf_path)
print("Characters extracted:", len(text))
print("Valid PO Found:", "QAHE-PO" in text)
print(text)

