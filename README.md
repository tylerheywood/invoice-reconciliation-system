# AP Inbox Control System (V1.0.0)

A deterministic, auditable Accounts Payable (AP) inbox triage and control system built in Python, backed by SQLite.

This system provides **visibility and control over invoices received into an Outlook AP inbox before they enter the ERP**, without using AI or OCR.  
It is designed to be explainable, repeatable, and safe to operate in regulated finance environments.

---

## 🎯 Purpose

AP teams often lack visibility over:
- what invoices have been received
- which are ready to post
- which are blocked (missing PO, invalid PO, unreadable PDF)
- the value and age of invoices sitting in the inbox

This tool addresses that gap by:
- treating each **email message as a unit of work**
- extracting and validating invoice facts deterministically
- surfacing reliable signals via a local dashboard

This is **not** an ERP replacement and does **not** post invoices.

---

## 🧱 Design Principles

- **Deterministic** – same inputs always produce the same outputs
- **Auditable** – every decision is explainable
- **Idempotent** – safe to re-run without duplication
- **No AI** – no probabilistic behaviour
- **No OCR (V1)** – unreadable PDFs are classified, not guessed
- **SQLite-backed** – simple, portable system of record

---

## 🧩 System Overview

### Unit of Work
- **Inbox email message**
- PDF attachments treated as invoice documents

### High-level Flow
1. Scan Outlook inbox folder(s)
2. Save PDF attachments to a local staging area
3. Hash PDFs (`sha256`) → `document_hash`
4. Write message + invoice metadata to SQLite
5. Extract:
   - PO numbers (regex-based)
   - Invoice values (net / VAT / gross where available)
6. Validate detected POs against `po_master`
7. Surface metrics via a local Streamlit dashboard

---

## 🗄️ Data Model (Core Tables)

- `inbox_message`  
  Email-level metadata and presence tracking

- `inbox_invoice`  
  Document-level facts (keyed by `document_hash`)

- `invoice_po`  
  Detected PO numbers (1:N per invoice)

- `invoice_resolution`  
  Human decisions / terminal outcomes (V1: minimal)

- `po_master`  
  Authoritative list of valid POs

- `supplier_master`  
  Supplier reference data (V2 usage)

---

## 🔑 Key Concepts

### Document Identity
- Invoices are identified by **SHA-256 hash of the PDF**
- Prevents duplication across re-runs or email moves

### Presence Awareness
- `is_currently_present` flips when emails leave the inbox
- Allows accurate “what’s waiting right now” reporting

### PO Match Status
Invoices are classified deterministically:

- `UNSCANNED`
- `NO_TEXT_LAYER`
- `MISSING_PO`
- `MULTIPLE_POS`
- `SINGLE_PO_DETECTED`
- `VALID_PO`
- `INVALID_PO`
- `FILE_MISSING`

Only `VALID_PO` invoices are considered **ready to post**.

---

## 💷 Value Extraction

- Extracts `net_total`, `vat_total`, `gross_total` where present
- Values stored as **integer pence** for accuracy
- Invoices without readable text remain unvalued
- Dashboard estimates missing values transparently using medians

---

## 📊 Dashboard

Built with **Streamlit** (local only in V1).

### Overview Tab Includes:
- Total estimated exposure (£)
- Ready-to-post count and value
- Manual review count and value
- PO confidence %
- Value coverage %
- Largest invoice
- Oldest invoice

All estimates are **explicitly disclosed**.

---

## 📁 Staging & File Retention

PDFs are stored in a local **staging directory**, keyed by `document_hash`.

### Purpose
- Deterministic reprocessing
- Auditability
- Safe rule iteration

### Retention Policy (V1)
- PDFs are retained **only while required for processing**
- A PDF may be deleted when:
  - the invoice is no longer present in the inbox, **and**
  - a terminal resolution has been recorded

Cleanup is **explicit and intentional**, not automatic.

---

## 🔐 GDPR Considerations

- PDFs may contain personal data
- Storage is limited to the processing purpose only
- Retention rules are defined and enforceable
- No data is shared or transmitted externally

This system is intended for **controlled, internal AP use**.

---

## 🚧 Out of Scope (V1)

Explicitly excluded from V1:
- OCR
- AI / ML
- ERP posting
- Workflow orchestration
- Hosting / multi-user access
- VAT compliance checks
- Supplier–PO cross-validation

---

## 🛣️ Likely V2 Enhancements

- OCR module (separate, optional)
- Ageing buckets & daily snapshot trends
- Supplier / PO validation rules
- Secure hosted dashboard
- Formal resolution workflows
- Automated retention enforcement

---

## ▶️ Running the System (High Level)

1. Configure Outlook access
2. Run inbox scan
3. Run PO detection
4. Run PO validation
5. Run value extraction
6. Launch Streamlit dashboard

Exact commands depend on local setup.

---

## 📌 Status

This is an **active, evolving system** focused on correctness, control, and signal quality before scale.

V1 prioritises **trustworthy data over automation**.

---

## 👤 Author

Built by an AP practitioner with hands-on experience in:
- high-volume invoice processing
- ERP controls
- AP risk and exception management
