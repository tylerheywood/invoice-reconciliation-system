from db import get_connection

# TEMPORARY POLICY:
# Currently only 'Open order' POs are counted as VALID.
# This will be changed once 'In review' appears in ERP exports
# and behaviour is observed in real data.

def run_po_validation() -> dict:
    """
    Validates detected POs against po_master.

    ATM rule:
    - PO must exist in po_master
    - PO status must be exactly 'Open order'

    NOTE:
    This is a temporary policy based on current ERP exports.
    Approval / other statuses will be revisited once observable.
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            ii.document_hash,
            ip.po_number,
            pm.po_status
        FROM inbox_invoice ii
        JOIN invoice_po ip
            ON ii.document_hash = ip.document_hash
        LEFT JOIN po_master pm
            ON ip.po_number = pm.po_number
        WHERE ii.po_match_status = 'SINGLE_PO_DETECTED'
    """)

    rows = cur.fetchall()

    validated = 0
    valid = 0
    invalid = 0

    for row in rows:
        document_hash = row["document_hash"]
        po_status = row["po_status"]

        if po_status == "Open order":
            new_status = "VALID_PO"
            valid += 1
        else:
            new_status = "INVALID_PO"
            invalid += 1

        cur.execute("""
            UPDATE inbox_invoice
            SET po_match_status = ?
            WHERE document_hash = ?
        """, (new_status, document_hash))

        validated += 1

    conn.commit()

    return {
        "validated": validated,
        "valid": valid,
        "invalid": invalid,
    }
