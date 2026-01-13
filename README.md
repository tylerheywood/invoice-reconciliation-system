# InboxPOScanner

Prototype for scanning invoice PDFs and detecting valid Purchase Order numbers.

The system:
- reads invoice PDFs
- extracts all text
- finds PO numbers matching QAHE-PO-XXXXXX
- validates them against a Dynamics purchase order export

This is the core logic for AP inbox triage.

## Usage

1. Put Dynamics PO export into `data/`
2. Put invoice PDFs into `invoices/`
3. Run: **python main.py**

The script will print detected POs and whether they are valid.