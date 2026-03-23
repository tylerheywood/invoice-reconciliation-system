### Use this sequence to reset scanned emails register (during dev only)

# 1
### in Sql Execute

DELETE FROM invoice_po;

---
# 2
### in Sql Execute

UPDATE inbox_invoice

SET

    po_count = 0,
    po_match_status = 'UNSCANNED',
    last_scan_datetime = last_scan_datetime

WHERE is_currently_present = 1;