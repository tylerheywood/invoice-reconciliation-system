# Getting Started

A quick guide to running the Invoice Reconciliation System (IRS) for the first time.

---

## Prerequisites

- **Python 3.10+**
- **pip** (comes with Python)
- A PO master file (`Purchase_orders.csv`) in the `data/` folder
- Invoice PDFs you want to process

---

## 1. Clone and install

```bash
git clone https://github.com/tylerheywood/invoice-reconciliation-system.git
cd invoice-reconciliation-system
```

Create a virtual environment (recommended):

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r docs/requirements.txt
```

> **Note:** `pywin32` is listed in requirements but only needed on Windows. On macOS/Linux you can safely ignore the install warning for that package — the folder scanner does not use it.

---

## 2. Add your PO master data

Place your PO master CSV at:

```
data/Purchase_orders.csv
```

This is the authoritative list of valid purchase orders the system validates against. You can also upload a replacement via the dashboard's PO upload feature once the server is running.

---

## 3. Drop invoice PDFs into the input folder

Place your invoice PDFs in the `input/` folder at the project root:

```
input/
  ├── invoice_001.pdf
  ├── invoice_002.pdf
  └── subfolder/
      └── invoice_003.pdf
```

The scanner picks up PDFs recursively, so subfolders work fine.

> The `input/` folder is created automatically when you run the pipeline or the start script. You can also create it manually.

---

## 4. Run the pipeline

```bash
python main.py
```

This runs all stages in order:

1. Initialises the SQLite database (`inbox.db`)
2. Loads the PO master data
3. Scans `input/` for PDFs and copies them to `staging/`
4. Detects PO numbers in each invoice
5. Validates detected POs against the PO master
6. Extracts invoice values (net, VAT, gross)
7. Refreshes the worklist
8. Writes `exports/snapshot.json` for the dashboard

---

## 5. Start the dashboard

```bash
python app.py
```

Or use the convenience scripts:

```bash
# Windows
start.bat

# macOS / Linux
./start.sh
```

The dashboard opens at **http://localhost:5000**.

> The start scripts install dependencies and launch the Flask server in one step.

---

## Typical workflow

1. Drop invoice PDFs into `input/`
2. Run `python main.py` to process them
3. Open the dashboard to review results
4. Invoices with a valid, open, approved PO appear as **READY TO POST**
5. Everything else appears as **MANUAL REVIEW** with an explanation

Re-run `main.py` any time you add new invoices or update the PO master. The system is idempotent — duplicate PDFs are detected by SHA-256 hash and skipped.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `ICS_INPUT_DIR` | `./input` | Folder to scan for invoice PDFs |
| `ICS_DEBUG` | off | Set to `1` for verbose pipeline output |

Example:

```bash
ICS_DEBUG=1 ICS_INPUT_DIR=/path/to/invoices python main.py
```

---

## Project structure

```
├── input/              ← Drop invoice PDFs here
├── staging/            ← Pipeline working copy of PDFs
├── data/               ← PO master CSV
├── exports/            ← snapshot.json for the dashboard
├── main.py             ← Pipeline entry point
├── folder_scanner.py   ← Ingests PDFs from input/
├── app.py              ← Flask server for the dashboard
├── dashboard.html      ← Dashboard UI
├── db.py               ← SQLite schema and connections
├── inbox.db            ← SQLite database (created at runtime)
└── docs/               ← Documentation
```
