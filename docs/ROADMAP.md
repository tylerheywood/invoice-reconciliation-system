# Roadmap — Recommended Next Steps

## Functionality

### 1. Duplicate invoice detection
Flag invoices where the same supplier + same gross total appears more than once within a configurable window (e.g. 30 days). This is the single most common AP error — paying the same invoice twice. The data is already in the database; this just needs a detection pass after value extraction and a new worklist reason.

### 2. Scheduled pipeline runs
Right now the pipeline is manual — you run `python main.py` each time. A built-in scheduler (even just a simple loop with a configurable interval) would let IRS watch the input folder continuously and re-process on a cadence. This removes the biggest friction point for daily use without requiring OS-level cron/Task Scheduler setup.

### 3. Invoice archival and lifecycle
Once an invoice is posted in the ERP, there's no way to mark it as done in IRS. Adding a simple "mark as posted" action (via the dashboard or a CSV upload of posted document hashes) would let completed invoices drop off the worklist cleanly and enable accurate historical reporting on throughput and cycle times.

### 4. Multi-format PO pattern configuration
The PO detection regex is currently hardcoded to a single format (QAHE-PO-XXXXXX). Different organisations use different PO formats. Extracting the pattern definitions into a config file (JSON or YAML) would make IRS usable for any organisation without code changes.

### 5. Email/webhook notifications for exceptions
When the pipeline detects a new manual review item (missing PO, unreadable PDF, PO not in master), it should be able to notify someone. A simple webhook POST or email alert for new exceptions would close the loop between "the system found a problem" and "someone acts on it" — especially useful when running on a schedule.

---

## UX

### 1. Worklist inline actions
The worklist currently shows what needs attention but offers no way to act on it. Adding a "Mark as reviewed" or "Add note" button per row would turn the dashboard from a read-only view into an operational tool. Even a simple notes field per invoice would save AP teams from tracking exceptions in a separate spreadsheet.

### 2. Dashboard auto-refresh
The dashboard only updates when you manually reload the page. Adding a polling interval (fetch snapshot.json every 30-60 seconds) or a WebSocket push would make the dashboard feel live, especially when running the pipeline on a schedule. A small "Last refreshed" indicator and a manual refresh button would complete the UX.

### 3. Invoice PDF preview
When reviewing an exception, the user currently has to go find the PDF in the staging folder manually. Embedding a PDF viewer (or even just a link to download the staged file) directly in the worklist row would dramatically reduce the time to investigate and resolve each item.

### 4. Export to CSV
Finance teams live in Excel. A "Download as CSV" button on the worklist, PO master, and invoices tables would make IRS data immediately usable in existing workflows — pivot tables, email attachments to managers, audit evidence packs. The data is already structured; this is just a serialisation step.

### 5. Filter and search across all tabs
The Data tab has filtering, but the worklist, exceptions, and ageing tabs don't. Adding a universal search bar or per-tab filters (by PO number, filename, date range, status) would make the dashboard usable at scale. With 28,000 POs and growing invoice volumes, finding a specific item without filtering is painful.
