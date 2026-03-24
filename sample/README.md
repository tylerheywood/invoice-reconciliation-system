# Sample Dataset

A set of 20 invoice PDFs and a PO master file for demonstrating and testing the Invoice Reconciliation System.

## Quick start

```bash
# Copy invoices into the input folder
cp -r sample/invoices/* input/

# Copy PO master into the data folder
mkdir -p data
cp sample/po_master.csv data/Purchase_orders.csv

# Run the pipeline
python main.py

# Start the dashboard
python app.py
```

## What each invoice demonstrates

### Group 1 — Clean matches (INV-001 to INV-010)
10 invoices with valid PO references that exist in the PO master with "Open order" and "Confirmed" status. These should flow through as **Ready to Post** after the pipeline runs.

| File | Supplier | PO | Gross |
|------|----------|----|-------|
| INV-001.pdf | Alpha Supplies Ltd | ORG-PO-000001 | £5,040.00 |
| INV-002.pdf | Alpha Supplies Ltd | ORG-PO-000002 | £2,220.00 |
| INV-003.pdf | Beta Services Ltd | ORG-PO-000003 | £11,280.00 |
| INV-004.pdf | Beta Services Ltd | ORG-PO-000004 | £3,780.00 |
| INV-005.pdf | Gamma Works Ltd | ORG-PO-000005 | £8,040.00 |
| INV-006.pdf | Gamma Works Ltd | ORG-PO-000006 | £2,760.00 |
| INV-007.pdf | Delta Group Ltd | ORG-PO-000007 | £13,800.00 |
| INV-008.pdf | Delta Group Ltd | ORG-PO-000008 | £5,880.00 |
| INV-009.pdf | Epsilon Corp Ltd | ORG-PO-000009 | £8,700.00 |
| INV-010.pdf | Epsilon Corp Ltd | ORG-PO-000010 | £4,320.00 |

### Group 2 — PO not in master (INV-011 to INV-014)
4 invoices with PO references (ORG-PO-000099 to 000102) that do not exist in the PO master. These should appear as **Manual Review — PO Not Found**.

| File | Supplier | PO | Gross |
|------|----------|----|-------|
| INV-011.pdf | Zeta Logistics Ltd | ORG-PO-000099 | £6,480.00 |
| INV-012.pdf | Zeta Logistics Ltd | ORG-PO-000100 | £2,520.00 |
| INV-013.pdf | Eta Consulting Ltd | ORG-PO-000101 | £10,560.00 |
| INV-014.pdf | Eta Consulting Ltd | ORG-PO-000102 | £4,500.00 |

### Group 3 — No PO reference (INV-015, INV-016)
2 invoices with no PO number anywhere in the document. These should appear as **Manual Review — No PO Reference**.

| File | Supplier | Gross |
|------|----------|-------|
| INV-015.pdf | Theta Traders Ltd | £1,440.00 |
| INV-016.pdf | Theta Traders Ltd | £1,140.00 |

### Group 4 — Duplicate invoices (INV-017, INV-018)
2 invoices that match the gross total and supplier of earlier invoices, triggering duplicate detection.

| File | Supplier | Duplicates | Gross |
|------|----------|-----------|-------|
| INV-017.pdf | Alpha Supplies Ltd | Same gross as INV-001 | £5,040.00 |
| INV-018.pdf | Beta Services Ltd | Same gross as INV-003 | £11,280.00 |

### Group 5 — Multiple POs (INV-019)
1 invoice referencing two PO numbers (ORG-PO-000013 and ORG-PO-000014). This should appear as **Manual Review — Multiple POs Detected**.

| File | Supplier | POs | Gross |
|------|----------|-----|-------|
| INV-019.pdf | Iota Partners Ltd | ORG-PO-000013, ORG-PO-000014 | £7,320.00 |

### Group 6 — Unreadable PDF (INV-020)
1 PDF with no text layer (only a filled rectangle). This should appear as **Manual Review — Unreadable**.

## PO master

`po_master.csv` contains 14 PO records covering all suppliers in Groups 1, 4, and 5. PO numbers 000099–000102 (Group 2 suppliers) are deliberately absent to trigger the "PO Not Found" path.

## Regenerating

To regenerate the PDFs:

```bash
pip install reportlab
python sample/generate_samples.py
```
