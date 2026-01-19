def extract_po_numbers(text: str) -> set[str]:
    po_numbers = set()

    for word in text.split():
        cleaned = word.strip(".,;:()[]{}<>\"'")  # trims common trailing/leading punctuation

        if cleaned.startswith("QAHE-PO-"):
            suffix = cleaned.replace("QAHE-PO-", "")
            if suffix.isdigit() and len(suffix) == 6:     # identifies valid POs
                po_numbers.add(cleaned)

    return po_numbers

