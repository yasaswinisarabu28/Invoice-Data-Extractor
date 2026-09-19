import pymupdf

def extract_pages(pdf_path):
    """
    Extracts text from every page of a PDF, keeping track of page numbers.
    Returns a list of (page_number, text) tuples — skips pages with no
    extractable text (e.g., scanned image-only pages).
    """
    doc = pymupdf.open(pdf_path)
    pages = []

    for page_num, page in enumerate(doc, start=1):
        text = page.get_text().strip()

        if text:
            pages.append((page_num, text))
        else:
            print(f"Warning: page {page_num} has no extractable text (possibly scanned/image-only). Skipping.")

    doc.close()
    return pages


if __name__ == "__main__":
    pdf_path = "sample.pdf"
    result = extract_pages(pdf_path)
    print(f"Extracted text from {len(result)} pages.")
    print(result[0])
