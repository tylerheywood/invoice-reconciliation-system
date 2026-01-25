# AP Inbox Control System (V1.0.0)

---
> **TL;DR**  
> 
> ICS is a deterministic control layer for Accounts Payable inboxes.  
>  
> It makes invoice intake **visible, explainable, and auditable** before invoices reach the ERP —  
> without AI, OCR, or automation risk.  
>  
> ICS tells AP teams **what is ready to post, what is blocked, and why**,  
> reducing manual firefighting and restoring confidence at the point of entry.

---
## Executive Summary

The **AP Inbox Control System (ICS)** is a deterministic, auditable control layer for
Accounts Payable inboxes.

It provides **clear visibility and control over invoices at the point of entry** —
before they reach the ERP — without automation risk, AI, or probabilistic behaviour.

ICS is designed for finance environments where **explainability, repeatability,
and operational safety matter more than speed**.

---

### The Problem

In many AP teams, the inbox itself becomes an uncontrolled workflow:

- Invoices arrive without structure or visibility
- It is unclear which invoices are ready to post vs blocked
- Missing or invalid POs are discovered late
- Managers lack reliable insight into value, age, and risk sitting in the inbox

Traditional solutions attempt to solve this with:
- automation first
- opaque rules
- workflow engines layered on top of uncertain data

This often **reduces trust rather than improving control**.

---

### The Approach

ICS takes a different approach:

- Treats the **inbox as a system of record**, not just a message queue
- Extracts and validates invoice facts **deterministically**
- Persists explicit truth at each pipeline stage
- Derives a **single, actionable next step per invoice**

No AI.  
No OCR (V1).  
No hidden decision-making.

Every outcome is explainable.

---

### What ICS Does (V1)

- Scans Outlook inbox folders and tracks invoice presence
- Identifies invoice documents via SHA-256 hashing
- Detects PO numbers using deterministic, regex-based rules
- Validates detected POs against authoritative master data
- Extracts invoice values using strict, auditable parsing
- Produces a live worklist showing exactly what needs human action

---

### What ICS Does Not Do (V1)

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
    - [What ICS Does (V1)](#what-ics-does-v1)
    - [What ICS Does Not Do (V1)](#what-ics-does-not-do-v1)

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
    - [8.3 Value Expectations (V1)](#83-value-expectations-v1)
    - [8.4 Live Validation Behaviour](#84-live-validation-behaviour)
    - [8.5 Persistence Model](#85-persistence-model)
  - **[9.0 Dashboard (Clarification)](#90-dashboard-clarification)**
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
- **Inbox email message**
- PDF attachments treated as invoice documents

### 2.2 High-level Flow
1. Scan Outlook inbox folder(s)
2. Save PDF attachments to a local staging area
3. Hash PDFs (`sha256`) → `document_hash`
4. Write message + invoice metadata to SQLite
5. Extract deterministically:
   - PO numbers (regex-based)
   - Invoice values (net / VAT / gross where available)
6. Validate detected POs against `po_master`
7. Surface metrics and status via a local Streamlit dashboard

---


## 3.0 Data Model (Core Tables)

---

- `inbox_message`  
  Email-level metadata and presence tracking

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
- `is_currently_present` flips when emails leave the inbox
- Enables accurate “what’s waiting right now” reporting

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

### 4.5 Ready-to-Post Flag
- `ready_to_post` is a **canonical truth column**
- Set only when:
  - exactly one PO is detected **and**
  - the PO exists in `po_master` **and**
  - the PO is in an open status
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

- Produces a **deterministic, actionable worklist** for Accounts Payable
- Translates pipeline truth into **exactly one next action per invoice**
- Designed for **human clarity**, not automation
- The worklist is a **derived view**, not a source of truth

The worklist answers one question only:

> *“What is the single next thing a human needs to do with this invoice?”*

---

### 8.1 Design Principles

- **Computed, not guessed**
  - No heuristic scoring
  - No hidden workflow state
  - Actions are derived directly from persisted invoice truth

- **One action per invoice**
  - Uses precedence-based rules (“first blocker wins”)
  - Avoids duplicate or competing tasks

- **AP-native semantics**
  - Actions reflect real AP work (e.g. *Request PO*, *Check PO Status*)
  - No generic workflow abstractions

- **Auditability over convenience**
  - Every action is explainable via stored invoice state
  - Historical worklist snapshots are retained

---

### 8.2 Worklist Actions (V1)

Each invoice is mapped to one of the following actions:

- `READY TO POST`
- `MANUAL REVIEW`

Each row also includes an **action reason**, explaining *why* the action was assigned
(e.g. `PO NOT OPEN`, `GROSS TOTAL NOT EXTRACTED`, `NO TEXT LAYER`).

---

### 8.3 Value Expectations (V1)

- Invoices are considered value-complete if **`gross_total` is present**
- **Gross-only invoices are valid** and supported (common for international suppliers)
- `net_total` and `vat_total` may be NULL and are not required in V1
- No value estimation or inference is performed at the worklist level

---

### 8.4 Live Validation Behaviour

- The worklist reflects the **current operational truth**
- PO validation is re-evaluated for all in-flight invoices on each run
- If a PO’s status changes in the master data:
  - the invoice’s validation status updates
  - the worklist action updates accordingly
- Invoices marked as posted (`posted_datetime IS NOT NULL`) are treated as terminal

---

### 8.5 Persistence Model

- `invoice_worklist`
  - Current, full-replace cache of actionable work
  - Represents the *latest* view of what needs attention

- `invoice_worklist_history`
  - Append-only snapshots of each run
  - Provides a complete audit trail of:
    - action changes
    - regressions (e.g. READY → BLOCKED)
    - operational trends over time

No manual removal or dismissal is supported in V1.
Items leave the worklist only when underlying truth changes.




---

## 9.0 Dashboard (Clarification)

The Streamlit dashboard is **read-only** and consumes database truth.

---

## 10.0 System Pipeline (Updated Order)

---
1. Initialise database schema
2. Load PO master data
3. Scan Outlook inbox
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
