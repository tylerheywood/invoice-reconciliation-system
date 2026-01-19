import pdfplumber

def pdf_to_text(pdf_path: str) -> str:

    text_parts = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:              # skip None pages
                text_parts.append(page_text)

    full_text = "\n".join(text_parts).upper()
    return full_text
