import pandas as pd

def load_po_set(csv_path: str) -> set[str]:
    df = pd.read_csv(csv_path, encoding="latin1")

    if "Purchase order" not in df.columns:
        raise ValueError("PO file does not contain 'Purchase order' column")

    po_series = df["Purchase order"]

    po_series = po_series.astype(str)
    po_series = po_series.str.strip()
    po_series = po_series.str.upper()

    po_series = po_series[po_series != "NAN"]
    po_series = po_series[po_series != ""]

    return set(po_series)
