from db import get_connection

VALID_OPEN_STATUS = "Open order"

STATUS_SINGLE_PO_DETECTED = "SINGLE_PO_DETECTED"

STATUS_UNVALIDATED = "UNVALIDATED"
STATUS_PO_NOT_IN_MASTER = "PO_NOT_IN_MASTER"
STATUS_PO_NOT_OPEN = "PO_NOT_OPEN"
STATUS_VALID_PO = "VALID_PO"


def run_po_validation() -> dict:
    """
    Validates detected POs against po_master.

    Rules:
    - Detection must be SINGLE_PO_DETECTED (po_match_status)
    - PO must exist in po_master
    - PO status must be 'Open order'

    Writes:
    - po_validation_status (truth of validation)
    - ready_to_post (canonical dashboard/worklist flag)
    """
    conn = get_connection()
    cur = conn.cursor()

    # Defensive: anything not SINGLE_PO_DETECTED cannot be "ready"
    cur.execute(
        """
        UPDATE inbox_invoice
        SET ready_to_post = 0
        WHERE is_currently_present = 1
          AND (po_match_status IS NULL OR po_match_status <> ?)
        """,
        (STATUS_SINGLE_PO_DETECTED,),
    )

    cur.execute(
        """
        SELECT
            ii.document_hash,
            ip.po_number,
            pm.po_status
        FROM inbox_invoice ii
        JOIN invoice_po ip
            ON ii.document_hash = ip.document_hash
        LEFT JOIN po_master pm
            ON ip.po_number = pm.po_number
        WHERE ii.po_match_status = ?
          AND ii.po_validation_status = ?
        """,
        (STATUS_SINGLE_PO_DETECTED, STATUS_UNVALIDATED),
    )

    rows = cur.fetchall()

    validated = valid = not_in_master = not_open = 0

    for row in rows:
        document_hash = row["document_hash"]
        po_status = row["po_status"]

        if po_status is None:
            new_validation_status = STATUS_PO_NOT_IN_MASTER
            ready_to_post = 0
            not_in_master += 1

        elif po_status != VALID_OPEN_STATUS:
            new_validation_status = STATUS_PO_NOT_OPEN
            ready_to_post = 0
            not_open += 1

        else:
            new_validation_status = STATUS_VALID_PO
            ready_to_post = 1
            valid += 1

        cur.execute(
            """
            UPDATE inbox_invoice
            SET po_validation_status = ?,
                ready_to_post = ?
            WHERE document_hash = ?
            """,
            (new_validation_status, ready_to_post, document_hash),
        )

        validated += 1

    conn.commit()
    conn.close()

    return {
        "validated": validated,
        "valid": valid,
        "po_not_in_master": not_in_master,
        "po_not_open": not_open,
    }
