"""
Generate 20 sample invoice PDFs for the IRS demo dataset.
Run: python sample/generate_samples.py
"""

import os
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, black, white
from reportlab.pdfgen.canvas import Canvas

OUT_DIR = Path(__file__).resolve().parent / "invoices"
OUT_DIR.mkdir(parents=True, exist_ok=True)

WIDTH, HEIGHT = A4

# Colours
DARK = HexColor("#1a1a2e")
ACCENT = HexColor("#16213e")
MUTED = HexColor("#555555")
LIGHT_BG = HexColor("#f8f9fa")


def draw_invoice(filename, supplier_name, supplier_address, invoice_no, date,
                 bill_to, line_items, net_total, vat_amount, gross_total,
                 po_reference=None, extra_text=None):
    """Draw a realistic invoice PDF."""
    path = OUT_DIR / filename
    c = Canvas(str(path), pagesize=A4)

    # --- Header background ---
    c.setFillColor(DARK)
    c.rect(0, HEIGHT - 95*mm, WIDTH, 95*mm, fill=1, stroke=0)

    # --- Supplier info (top left) ---
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(20*mm, HEIGHT - 18*mm, supplier_name)
    c.setFont("Helvetica", 9)
    y = HEIGHT - 25*mm
    for line in supplier_address:
        c.drawString(20*mm, y, line)
        y -= 4.5*mm

    # --- INVOICE heading (top right) ---
    c.setFont("Helvetica-Bold", 28)
    c.drawRightString(WIDTH - 20*mm, HEIGHT - 22*mm, "INVOICE")

    c.setFont("Helvetica", 10)
    c.setFillColor(HexColor("#cccccc"))
    c.drawRightString(WIDTH - 20*mm, HEIGHT - 32*mm, f"Invoice No:  {invoice_no}")
    c.drawRightString(WIDTH - 20*mm, HEIGHT - 38*mm, f"Date:  {date}")

    if po_reference:
        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(HexColor("#4fc3f7"))
        c.drawRightString(WIDTH - 20*mm, HEIGHT - 46*mm, f"Purchase Order:  {po_reference}")

    # --- Bill To ---
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20*mm, HEIGHT - 60*mm, "BILL TO:")
    c.setFont("Helvetica", 9)
    y = HEIGHT - 66*mm
    for line in bill_to:
        c.drawString(20*mm, y, line)
        y -= 4.5*mm

    # --- Line items table ---
    table_top = HEIGHT - 105*mm
    c.setFillColor(ACCENT)
    c.rect(15*mm, table_top - 2*mm, WIDTH - 30*mm, 10*mm, fill=1, stroke=0)

    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20*mm, table_top + 1*mm, "Description")
    c.drawRightString(130*mm, table_top + 1*mm, "Qty")
    c.drawRightString(160*mm, table_top + 1*mm, "Unit Price")
    c.drawRightString(WIDTH - 20*mm, table_top + 1*mm, "Amount")

    c.setFillColor(black)
    c.setFont("Helvetica", 9)
    y = table_top - 12*mm
    for desc, qty, unit_price, amount in line_items:
        c.drawString(20*mm, y, desc)
        c.drawRightString(130*mm, y, str(qty))
        c.drawRightString(160*mm, y, f"£{unit_price:,.2f}")
        c.drawRightString(WIDTH - 20*mm, y, f"£{amount:,.2f}")
        # Row separator
        c.setStrokeColor(HexColor("#e0e0e0"))
        c.setLineWidth(0.3)
        c.line(15*mm, y - 3*mm, WIDTH - 15*mm, y - 3*mm)
        y -= 10*mm

    # Extra text (e.g. multi-PO notes)
    if extra_text:
        c.setFont("Helvetica", 8)
        c.setFillColor(MUTED)
        for line in extra_text:
            c.drawString(20*mm, y, line)
            y -= 5*mm

    # --- Totals ---
    totals_y = y - 10*mm
    c.setFillColor(LIGHT_BG)
    c.rect(120*mm, totals_y - 5*mm, WIDTH - 135*mm, 38*mm, fill=1, stroke=0)

    c.setFillColor(black)
    c.setFont("Helvetica", 10)
    c.drawRightString(160*mm, totals_y + 25*mm, "Net Total:")
    c.drawRightString(WIDTH - 20*mm, totals_y + 25*mm, f"£{net_total:,.2f}")

    c.drawRightString(160*mm, totals_y + 14*mm, "VAT (20%):")
    c.drawRightString(WIDTH - 20*mm, totals_y + 14*mm, f"£{vat_amount:,.2f}")

    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(160*mm, totals_y + 1*mm, "Gross Total:")
    c.drawRightString(WIDTH - 20*mm, totals_y + 1*mm, f"£{gross_total:,.2f}")

    # --- Footer ---
    c.setFont("Helvetica", 8)
    c.setFillColor(MUTED)
    c.drawString(20*mm, 20*mm, "Payment terms: 30 days. Please quote invoice number on remittance.")
    c.drawString(20*mm, 15*mm, f"{supplier_name}  |  Company Reg: {hash(supplier_name) % 9000000 + 1000000}")

    c.save()
    print(f"  Created {filename}")


def draw_blank_pdf(filename):
    """Create a PDF with no text layer — just a filled rectangle."""
    path = OUT_DIR / filename
    c = Canvas(str(path), pagesize=A4)
    c.setFillColor(HexColor("#e8e8e8"))
    c.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)
    # Small dark rectangle to make it look like a scanned blank page
    c.setFillColor(HexColor("#d0d0d0"))
    c.rect(20*mm, 20*mm, WIDTH - 40*mm, HEIGHT - 40*mm, fill=1, stroke=0)
    c.save()
    print(f"  Created {filename} (no text layer)")


BILL_TO = [
    "Sample Organisation Ltd",
    "1 Business Park",
    "London, EC1A 1BB",
    "United Kingdom",
]


def make_items(descriptions_and_prices, net_target):
    """Build line items that sum to the net target."""
    items = []
    running = 0.0
    for i, (desc, qty, up) in enumerate(descriptions_and_prices):
        if i == len(descriptions_and_prices) - 1:
            # Last item absorbs rounding
            amount = round(net_target - running, 2)
            up = round(amount / qty, 2)
        else:
            amount = round(qty * up, 2)
            running += amount
        items.append((desc, qty, up, amount))
    return items


def main():
    print("Generating sample invoices...")

    # ── Group 1: Clean matches ──
    g1 = [
        ("INV-001.pdf", "Alpha Supplies Ltd", "INV-2026-001", "ORG-PO-000001", 4200.00,
         [("Office furniture — standing desks", 6, 500.00), ("Ergonomic chairs", 4, 200.00), ("Monitor arms", 10, 20.00)]),
        ("INV-002.pdf", "Alpha Supplies Ltd", "INV-2026-002", "ORG-PO-000002", 1850.00,
         [("Printer cartridges (bulk)", 50, 25.00), ("A4 copy paper (boxes)", 30, 12.00), ("Binding supplies", 1, 490.00)]),
        ("INV-003.pdf", "Beta Services Ltd", "INV-2026-003", "ORG-PO-000003", 9400.00,
         [("IT support — March retainer", 1, 6500.00), ("Network cabling — Building B", 1, 1800.00), ("Firewall licence renewal", 1, 1100.00)]),
        ("INV-004.pdf", "Beta Services Ltd", "INV-2026-004", "ORG-PO-000004", 3150.00,
         [("Server maintenance Q1", 1, 2000.00), ("SSL certificate renewals", 5, 130.00), ("Cloud backup — March", 1, 500.00)]),
        ("INV-005.pdf", "Gamma Works Ltd", "INV-2026-005", "ORG-PO-000005", 6700.00,
         [("Electrical installation — Floor 3", 1, 4200.00), ("PAT testing (200 items)", 200, 5.50), ("Emergency lighting check", 1, 1400.00)]),
        ("INV-006.pdf", "Gamma Works Ltd", "INV-2026-006", "ORG-PO-000006", 2300.00,
         [("Plumbing repair — kitchen", 1, 850.00), ("Boiler service", 1, 450.00), ("Water heater replacement", 1, 1000.00)]),
        ("INV-007.pdf", "Delta Group Ltd", "INV-2026-007", "ORG-PO-000007", 11500.00,
         [("Consultancy — strategy review", 5, 1800.00), ("Workshop facilitation", 2, 1000.00), ("Final report production", 1, 500.00)]),
        ("INV-008.pdf", "Delta Group Ltd", "INV-2026-008", "ORG-PO-000008", 4900.00,
         [("Recruitment — senior hire", 1, 3500.00), ("Background screening", 3, 200.00), ("Psychometric testing", 5, 140.00)]),
        ("INV-009.pdf", "Epsilon Corp Ltd", "INV-2026-009", "ORG-PO-000009", 7250.00,
         [("Catering — annual conference", 150, 35.00), ("AV equipment hire", 1, 1500.00), ("Venue decoration", 1, 500.00)]),
        ("INV-010.pdf", "Epsilon Corp Ltd", "INV-2026-010", "ORG-PO-000010", 3600.00,
         [("Staff training — compliance", 20, 120.00), ("Training materials", 20, 30.00), ("Certification fees", 20, 30.00)]),
    ]

    addresses = {
        "Alpha Supplies Ltd": ["42 Commerce Street", "Manchester, M1 4BT", "United Kingdom"],
        "Beta Services Ltd": ["8 Technology Park", "Cambridge, CB1 2QQ", "United Kingdom"],
        "Gamma Works Ltd": ["15 Industrial Estate", "Birmingham, B4 7DA", "United Kingdom"],
        "Delta Group Ltd": ["3 Consulting Row", "Edinburgh, EH1 3AA", "United Kingdom"],
        "Epsilon Corp Ltd": ["99 Event Square", "Bristol, BS1 5NP", "United Kingdom"],
        "Zeta Logistics Ltd": ["7 Freight Lane", "Southampton, SO14 2AA", "United Kingdom"],
        "Eta Consulting Ltd": ["22 Strategy Place", "Leeds, LS1 4HR", "United Kingdom"],
        "Theta Traders Ltd": ["55 Market Road", "Liverpool, L1 8JQ", "United Kingdom"],
        "Iota Partners Ltd": ["11 Alliance Court", "Glasgow, G2 4JR", "United Kingdom"],
    }

    for i, (fname, supplier, inv_no, po, net, item_defs) in enumerate(g1):
        items = make_items(item_defs, net)
        vat = round(net * 0.20, 2)
        gross = round(net + vat, 2)
        date = f"{(i % 28) + 1:02d} March 2026"
        draw_invoice(fname, supplier, addresses[supplier], inv_no, date,
                     BILL_TO, items, net, vat, gross, po_reference=po)

    # ── Group 2: PO not in master ──
    g2 = [
        ("INV-011.pdf", "Zeta Logistics Ltd", "INV-2026-011", "ORG-PO-000099", 5400.00,
         [("Freight — 20ft container", 2, 2200.00), ("Customs clearance", 1, 600.00), ("Insurance", 1, 400.00)]),
        ("INV-012.pdf", "Zeta Logistics Ltd", "INV-2026-012", "ORG-PO-000100", 2100.00,
         [("Pallet delivery (local)", 10, 150.00), ("Same-day courier", 3, 100.00), ("Packaging materials", 1, 300.00)]),
        ("INV-013.pdf", "Eta Consulting Ltd", "INV-2026-013", "ORG-PO-000101", 8800.00,
         [("Market research report", 1, 5000.00), ("Competitor analysis", 1, 2500.00), ("Presentation deck", 1, 1300.00)]),
        ("INV-014.pdf", "Eta Consulting Ltd", "INV-2026-014", "ORG-PO-000102", 3750.00,
         [("HR policy review", 1, 2000.00), ("Employment law update", 1, 1250.00), ("Staff handbook revision", 1, 500.00)]),
    ]

    for i, (fname, supplier, inv_no, po, net, item_defs) in enumerate(g2):
        items = make_items(item_defs, net)
        vat = round(net * 0.20, 2)
        gross = round(net + vat, 2)
        date = f"{(i + 12) % 28 + 1:02d} March 2026"
        draw_invoice(fname, supplier, addresses[supplier], inv_no, date,
                     BILL_TO, items, net, vat, gross, po_reference=po)

    # ── Group 3: No PO reference ──
    g3 = [
        ("INV-015.pdf", "Theta Traders Ltd", "INV-2026-015", 1200.00,
         [("Miscellaneous office supplies", 1, 800.00), ("Kitchen consumables", 1, 400.00)]),
        ("INV-016.pdf", "Theta Traders Ltd", "INV-2026-016", 950.00,
         [("Window cleaning — Q1", 1, 650.00), ("Carpet cleaning", 1, 300.00)]),
    ]

    for i, (fname, supplier, inv_no, net, item_defs) in enumerate(g3):
        items = make_items(item_defs, net)
        vat = round(net * 0.20, 2)
        gross = round(net + vat, 2)
        date = f"{(i + 18) % 28 + 1:02d} March 2026"
        draw_invoice(fname, supplier, addresses[supplier], inv_no, date,
                     BILL_TO, items, net, vat, gross, po_reference=None)

    # ── Group 4: Duplicates (same supplier + same gross as Group 1) ──
    # INV-017: same as INV-001 (Alpha, net £4,200.00, gross £5,040.00)
    items_017 = make_items([
        ("Office furniture — replacement desks", 4, 750.00), ("Delivery charge", 1, 150.00), ("Assembly service", 1, 1050.00)
    ], 4200.00)
    draw_invoice("INV-017.pdf", "Alpha Supplies Ltd", addresses["Alpha Supplies Ltd"],
                 "INV-2026-017", "22 March 2026", BILL_TO, items_017,
                 4200.00, 840.00, 5040.00, po_reference="ORG-PO-000011")

    # INV-018: same as INV-003 (Beta Services, net £9,400.00, gross £11,280.00)
    items_018 = make_items([
        ("IT security audit", 1, 5500.00), ("Penetration testing", 1, 2400.00), ("Remediation support", 1, 1500.00)
    ], 9400.00)
    draw_invoice("INV-018.pdf", "Beta Services Ltd", addresses["Beta Services Ltd"],
                 "INV-2026-018", "24 March 2026", BILL_TO, items_018,
                 9400.00, 1880.00, 11280.00, po_reference="ORG-PO-000003")

    # ── Group 5: Multiple POs ──
    items_019 = [
        ("Software licences (ref: ORG-PO-000013)", 10, 350.00, 3500.00),
        ("Implementation services (ref: ORG-PO-000014)", 1, 2600.00, 2600.00),
    ]
    draw_invoice("INV-019.pdf", "Iota Partners Ltd", addresses["Iota Partners Ltd"],
                 "INV-2026-019", "25 March 2026", BILL_TO, items_019,
                 6100.00, 1220.00, 7320.00, po_reference="ORG-PO-000013",
                 extra_text=["Note: This invoice covers two purchase orders: ORG-PO-000013 and ORG-PO-000014."])

    # ── Group 6: Unreadable (no text layer) ──
    draw_blank_pdf("INV-020.pdf")

    print(f"\nDone. {len(list(OUT_DIR.glob('*.pdf')))} PDFs created in {OUT_DIR}")


if __name__ == "__main__":
    main()
