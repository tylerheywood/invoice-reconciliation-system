from pathlib import Path
from csv_loader import load_po_set

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

po_set = load_po_set(DATA / "Purchase_orders.csv")

print(f"{len(po_set)} POs loaded")
print(list(po_set)[:5])
