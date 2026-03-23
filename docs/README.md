# Invoice Reconciliation System (V1.0.0)

---
> **TL;DR**
>
> IRS is a deterministic reconciliation layer for Accounts Payable invoices.
>
> It makes invoice intake **visible, explainable, and auditable** before invoices reach the ERP —
> without AI, OCR, or automation risk.
>
> IRS tells AP teams **what is ready to post, what is blocked, and why**,
> reducing manual firefighting and restoring confidence at the point of entry.

---
## Executive Summary

The **Invoice Reconciliation System (IRS)** is a deterministic, auditable reconciliation layer for
Accounts Payable invoices.

It provides **clear visibility and control over invoices at the point of entry** —
before they reach the ERP — without automation risk, AI, or probabilistic behaviour.

IRS is designed for finance environments where **explainability, repeatability,
and operational safety matter more than speed**.

---

### The Problem

In many AP teams, invoice intake becomes an uncontrolled workflow:

- Invoices arrive without structure or visibility
- It is unclear which invoices are ready to post vs blocked
- Missing or invalid POs are discovered late
- Managers lack reliable insight into value, age, and risk sitting in the queue

Traditional solutions attempt to solve this with:
- automation first
- opaque rules
- workflow engines layered on top of uncertain data

This often **reduces trust rather than improving control**.

---

### The Approach

IRS takes a different approach:

- Treats the **input folder as a system of record**, not just a file drop
- Extracts and validates invoice facts **deterministically**
- Persists explicit truth at each pipeline stage
- Derives a **single, actionable next step per invoice**

No AI.
No OCR (V1).
No hidden decision-making.

Every outcome is explainable.

---

### What IRS Does (V1)

- Scans a local input folder and tracks invoice presence
- Identifies invoice documents via SHA-256 hashing
- Detects PO numbers using deterministic, regex-based rules
- Validates detected POs against authoritative master data
- Extracts invoice values using strict, auditable parsing
- Produces a live worklist showing exactly what needs human action

---

### What IRS Does Not Do (V1)

- Does not post invoices to the ERP
- Does not guess or estimate missing values
- Does not use OCR or AI
- Does not enforce approval workflows
---
 ## Contents

---

- **Front Matter**
  - **[Executive Summary](#executive-summary)**
    - [The Problem](#the-problem)
    - [The Approach](#the-approach)
    - [What IRS Does (V1)](#what-irs-does-v1)
    - [What IRS Does Not Do (V1)](#what-irs-does-not-do-v1)

- **Body**
  - **[1.0 Design Principles](#10-design-principles)**
  - **[2.0 System Overview](#20-system-overview)**
    - [2.1 Unit of Work](#21-unit-of-work)
    - [2.2 High-level Flow](#22-high-level-flow)
  - **[3.0 Data Model (Core Tables)](#30-data-model-core-tables)**
  - **[4.0 Key Concepts](#40-key-concepts)**
    - [4.1 Document Identity](#41-document-identity)
    - [4.2 Presence Awareness](#42-presence-awareness)
    - [4.3 PO Detection Status (`po_match_status`)](#43-po-detection-status-po_match_status)
    - [4.4 PO Validation Status (`po_validation_status`)](#44-po-validation-status-po_validation_status)
    - [4.5 Ready-to-Post Flag](#45-ready-to-post-flag)
  - **[5.0 Value Extraction](#50-value-extraction)**
    - [5.1 Estimates](#51-estimates)
  - **[6.0 Debug Mode](#60-debug-mode)**
  - **[7.0 Clarifications & Constraints (V1)](#70-clarifications--constraints-v1)**
    - [7.1 PO Validation Behaviour](#71-po-validation-behaviour)
    - [7.2 Ready-to-Post Semantics](#72-ready-to-post-semantics)
    - [7.3 Value Extraction Constraints](#73-value-extraction-constraints)
  - **[8.0 Worklist (Job Queue)](#80-worklist-job-queue)**
    - [8.1 Design Principles](#81-design-principles)
    - [8.2 Worklist Actions (V1)](#82-worklist-actions-v1)
    - [8.3 Classification Model (V1)](#83-classification-model-v1)
    - [8.4 Value Expectations (V1)](#84-value-expectations-v1)
    - [8.5 Persistence Model](#85-persistence-model)
    - [8.6 Invoice Identification (V1)](#86-invoice-identification-v1)
    - [8.7 Live Behaviour](#87-live-behaviour)
  - **[9.0 Dashboard (Clarification)](#90-dashboard-clarification)**
    - [9.1 Architecture](#91-architecture)
    - [9.2 Scope and Constraints](#92-scope-and-constraints)
    - [9.3 Data Source](#93-data-source)
    - [9.4 Operational Intent](#94-operational-intent)
  - **[10.0 System Pipeline (Updated Order)](#100-system-pipeline-updated-order)**


- **Author Information**
  - **[11.0 Author](#110-author)**
    - [11.1 Experience](#111-experience)
    - [11.2 Contact Me](#112-contact-me)


---

## 1.0 Design Principles

---


- **Deterministic** – same inputs always produce the same outputs
- **Auditable** – every decision is explainable
- **Idempotent** – safe to re-run without duplication
- **No AI** – no probabilistic behaviour
- **No OCR (V1)** – unreadable PDFs are classified, not guessed
- **Explicit state** – pipeline stages write truth, dashboards consume it
- **SQLite-backed** – simple, portable system of record



---
## 2.0 System Overview

---
### 2.1 Unit of Work
- **Invoice PDF file** placed in the `./input` folder
- Each PDF is treated as an invoice document

### 2.2 High-level Flow
1. Scan the `./input` folder for PDF files (recursively)
2. Copy PDFs to a local staging area
3. Hash PDFs (`sha256`) → `document_hash`
4. Write message + invoice metadata to SQLite
5. Extract deterministically:
   - PO numbers (regex-based)
   - Invoice values (net / VAT / gross where available)
6. Validate detected POs against `po_master`
7. Surface metrics and status via a HTML dashboard

---


## 3.0 Data Model (Core Tables)

---

- `inbox_message`
  Ingestion-level metadata and presence tracking

- `inbox_invoice`
  Document-level facts (keyed by `document_hash`)

- `invoice_po`
  Detected PO numbers (1:N per invoice)

- `invoice_resolution`
  Human decisions / terminal outcomes (V1: minimal)

- `po_master`
  Authoritative list of valid POs (loaded at runtime)

- `supplier_master`
  Supplier reference data (reserved for V2 usage)

- `invoice_worklist`
  Live worklist, next actions and detailed tasking logic

- `invoice_worklist_history`
  Worklist history for analysis and reference


---
## 4.0 Key Concepts

---

### 4.1 Document Identity
- Invoices are identified by **SHA-256 hash of the PDF**
- Prevents duplication across re-runs, moves, or re-scans

### 4.2 Presence Awareness
- `is_currently_present` flips when invoices leave the input folder
- Enables accurate "what's waiting right now" reporting

### 4.3 PO Detection Status (`po_match_status`)
PO detection is **classification only** (not validation):

- `UNSCANNED`
- `NO_TEXT_LAYER`
- `MISSING_PO`
- `MULTIPLE_POS`
- `SINGLE_PO_DETECTED`
- `FILE_MISSING`

These states represent transient or infrastructural conditions and are not
treated as actionable AP work items in V1.

### 4.4 PO Validation Status (`po_validation_status`)
Validation is a **separate pipeline stage**, run only after detection:

- `UNVALIDATED`
- `PO_NOT_IN_MASTER`
- `PO_NOT_OPEN`
- `VALID_PO`
- `PO_NOT_CONFIRMED`

### 4.5 Ready-to-Post Flag
- `ready_to_post` is a **canonical truth column**
- Set only when:
  - exactly one PO is detected **and**
  - the PO exists in `po_master` **and**
  - the PO is in an open status
  - the PO approval status is `confirmed`
- Dashboards and worklists rely on this flag directly

---

## 5.0 Value Extraction

---
- Extracts `net_total`, `vat_total`, `gross_total` where present
- Values stored as **integer pence** for accuracy
- Uses **strict parsing rules** to avoid PO-like integers being misread as money
- Invoices without readable text are classified as `NO_TEXT_LAYER`
  and are not repeatedly reprocessed

### 5.1 Estimates
- Missing values are **not guessed**
- Dashboard-level estimates (e.g. medians) are:
  - clearly separated from actual values
  - explicitly disclosed

---

## 6.0 Debug Mode

---
A built-in debug mode exists for safe inspection during development.

- Enable via environment variable:
            "ICS_DEBUG=1"
- Or by toggling a local `DEBUG` flag in code

**Debug output includes:**
- candidate counts per pipeline stage
- truncated previews of extracted text
- rule decisions taken per invoice

Debug previews are **clipped by line and character length** to avoid log noise.

---

## 7.0 Clarifications & Constraints (V1)

---

This section clarifies operational constraints and edge-case behaviour for
concepts introduced earlier in the document.

No new pipeline stages or state are introduced here.

---

### 7.1 PO Validation Behaviour

- PO validation is **re-evaluated on each pipeline run** for all in-flight invoices
- Validation results always reflect the **latest `po_master` snapshot**
- An invoice previously marked as `VALID_PO` may regress to a blocked state
  if the PO becomes closed or invoiced
- Both PO lifecycle status and approval status are checked
- Invoices with `posted_datetime IS NOT NULL` are treated as **terminal**
  and excluded from re-validation

This ensures the system remains aligned to current ERP truth rather than
persisting stale approval state.

---

### 7.2 Ready-to-Post Semantics

- `ready_to_post` is a **derived truth flag**, not a workflow state
- It indicates that:
  - exactly one PO has been detected
  - the PO exists in `po_master`
  - the PO is currently open
  - the PO is approved
- The flag may flip **on or off** across runs as upstream truth changes
- Dashboards and worklists rely on this flag directly and do not infer readiness

---

### 7.3 Value Extraction Constraints

- Value extraction is **deterministic and strict**
- Only explicitly labelled monetary totals are extracted
- Values must include decimals (e.g. `123.45`)
- Gross-only invoices are supported and valid (common for international suppliers)
- Net and VAT values may be NULL without blocking progression
- Invoices without a readable text layer are classified as `NO_TEXT_LAYER`
  and excluded from repeated extraction attempts

No estimation or inference is performed at the extraction or worklist layer.

---
## 8.0 Worklist (Job Queue)

---

The worklist is a **deterministic, derived job queue** for Accounts Payable.

It translates persisted invoice truth into **exactly one actionable next step per invoice**.

The worklist does not introduce new state, approvals, or workflow logic.
It is a **computed view** over existing invoice data.

---

### 8.1 Design Principles

The worklist is designed around the following principles:

- **Computed, not guessed**
  - No heuristic scoring
  - No probabilistic decisions
  - Actions are derived directly from persisted invoice truth

- **One action per invoice**
  - Uses precedence-based rules ("first blocker wins")
  - Avoids competing or duplicated tasks

- **AP-native semantics**
  - Actions reflect real AP work (e.g. *Manual Review*, *Ready to Post*)
  - No abstract workflow states

- **Auditability over convenience**
  - Every action is explainable
  - Historical snapshots are retained for comparison and analysis

---

### 8.2 Worklist Actions (V1)

Each invoice is mapped to **one** of the following actions:

- `READY TO POST`
- `MANUAL REVIEW`

Each row also includes an **action reason** explaining *why* the action was assigned
(e.g. `PO NOT OPEN`, `MISSING PO`, `NO TEXT LAYER`, `GROSS TOTAL NOT EXTRACTED`).

The action reason is descriptive only and does not introduce additional workflow state.

---

### 8.3 Classification Model

Worklist classification is **precedence-based**.

Rules are evaluated in a fixed order.
The **first blocking condition wins** and determines the next action.

Examples of blocking conditions include:
- invoice not currently present in the input folder
- missing or unreadable text layer
- missing or invalid PO
- PO exists but is not open
- required value (gross total) not extracted

This model ensures consistent outcomes across re-runs and prevents rule conflicts.

---

### 8.4 Value Expectations

Invoices are considered value-complete if **`gross_total` is present**.

- Gross-only invoices are valid and supported
- `net_total` and `vat_total` may be NULL without blocking progression
- No estimation or inference is performed at the worklist level

Invoices missing a gross total are routed to `MANUAL REVIEW` with an explicit reason.

---

### 8.5 Persistence Model

The worklist uses two tables:

- `invoice_worklist`
  - Current, full-replace cache
  - Represents the latest actionable view

- `invoice_worklist_history`
  - Append-only snapshots per pipeline run
  - Provides a complete audit trail of:
    - action changes
    - regressions (e.g. READY → MANUAL)
    - operational trends

Items leave the worklist only when underlying invoice truth changes.

---

### 8.6 Invoice Identification

Each worklist row includes **human-identifiable context** so AP users can locate the
source invoice file.

Included identifiers:
- Source folder path
- Attachment filename
- Received/scanned datetime

These fields are **descriptive only**.

They do not participate in:
- classification logic
- prioritisation
- readiness decisions

This separation preserves determinism while making the worklist operationally usable.

---

### 8.7 Live Behaviour

The worklist reflects **current operational truth**.

- Recomputed on every pipeline run
- PO validation is re-evaluated against the latest master data
- Invoices may move between actions as upstream truth changes
- Posted or no-longer-present invoices naturally fall out of scope

No manual dismissal or override is supported in V1.

---

## 9.0 Dashboard (Clarification)

---

The dashboard is a **read-only operational view** of the system.

It does not perform validation, classification, estimation, or decision-making.

All business logic runs **inside the pipeline** and persists truth to SQLite.
The dashboard simply **renders a published snapshot** of that truth.

---

### 9.1 Architecture

- The pipeline generates a deterministic `snapshot.json` file at the end of each run
- The snapshot is published to a web server
- A static HTML dashboard fetches and renders the snapshot
- No backend logic exists in the dashboard layer

This design enforces a strict separation between:

- **Computation** (pipeline)
- **Presentation** (dashboard)

The dashboard can be refreshed, cached, or redeployed freely without affecting system behaviour.

---

### 9.2 Scope and Constraints

The dashboard is intentionally constrained:

- **Read-only**
- **No write access** to SQLite or source systems
- **No workflow state**
- **No side effects**

It cannot:
- modify invoice state
- override worklist actions
- trigger postings or automation

This ensures the dashboard remains **safe, explainable, and auditable**.

---

### 9.3 Data Source

The dashboard consumes a single input:

- `snapshot.json`

The snapshot includes:
- overview metrics
- status breakdowns
- ageing buckets
- worklist rows
- (optional) historical trends

If the snapshot is unavailable or invalid, the dashboard displays an error and **takes no action**.

---

### 9.4 Operational Intent

The dashboard exists to answer three questions only:

1. What invoices are currently in the queue?
2. Which invoices are ready vs blocked?
3. What human action is required next — and why?

It is designed to support:
- AP operational review
- manager visibility
- exception-driven prioritisation

It is **not** a workflow engine, approval system, or automation surface.

---

## 10.0 System Pipeline (Updated Order)

---
1. Initialise database schema
2. Load PO master data
3. Scan input folder for invoice PDFs
4. Run PO detection
5. Run PO validation
6. Run value extraction
7. Update/Refresh Worklist

 ---
## 11.0 Author

---
### 11.1 Experience

Built by an AP Practitioner with hands-on experience in:

* High-volume invoice processing

* ERP controls

* AP risk and exception management

* Financial forecasting

* Month-end accounting (P&L, balance sheet, accruals & prepayments)

* Python and SQL (duh)


### 11.2 Contact Me

This project is actively evolving.

For discussion, feedback, or professional enquiries:
- GitHub Issues (preferred for technical discussion)
- LinkedIn: https://www.linkedin.com/in/tyler-heywood
- Email: tyler@aphospital.co.uk

<p align="center">
  <img src="assets/author.png" alt="Calm AP energy" width="420">>
</p>

<p align="center"><em>

</em></p>
