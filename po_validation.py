from db import get_connection

VALID_OPEN_STATUS = "Open order"

def run_po_validation() -> dict:
    """
    Validates detected POs against po_master.

    Rules:
    - Detection must be SINGLE_PO_DETECTED
    - PO must exist in po_master
    - PO status must be 'Open order'
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
          AND ii.po_validation_status = 'UNVALIDATED'
    """)

    rows = cur.fetchall()

    validated = valid = not_in_master = not_open = 0

    for row in rows:
        document_hash = row["document_hash"]
        po_status = row["po_status"]

        if po_status is None:
            new_status = "PO_NOT_IN_MASTER"
            not_in_master += 1
        elif po_status != VALID_OPEN_STATUS:
            new_status = "PO_NOT_OPEN"
            not_open += 1
        else:
            new_status = "VALID_PO"
            valid += 1

        cur.execute("""
            UPDATE inbox_invoice
            SET po_validation_status = ?
            WHERE document_hash = ?
        """, (new_status, document_hash))

        validated += 1

    conn.commit()
    conn.close()

    return {
        "validated": validated,
        "valid": valid,
        "po_not_in_master": not_in_master,
        "po_not_open": not_open,
    }
