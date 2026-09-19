import os
import json
import re
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import BaseModel, ValidationError, model_validator
from groq import Groq
from dotenv import load_dotenv

from extract import extract_pages  # reused from Project 1

load_dotenv()
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))


# --- Schema ---

class LineItem(BaseModel):
    description: str
    quantity: float   # float, not int — real quantities can be fractional (12.47 L fuel, 1.5 kg produce)
    unit_price: float
    total: float


class Adjustment(BaseModel):
    label: str      # e.g. "Discount", "Shipping", "Rounding"
    amount: float   # negative for a discount, positive for a surcharge/fee


class Invoice(BaseModel):
    vendor_name: str
    invoice_number: str
    invoice_date: str            # kept as plain string — real invoices use inconsistent date formats
    billed_to: str
    line_items: List[LineItem]
    subtotal: float
    adjustments: List[Adjustment] = []   # catch-all for discounts, shipping, rounding, etc.
    tax: Optional[float] = 0.0    # defaults to 0 if the invoice has no tax line
    total_amount: float

    @model_validator(mode="after")
    def check_totals_add_up(self):
        """
        Custom validation: catches cases where the LLM extracted the right
        FIELDS but got the MATH wrong (a common LLM failure mode).
        Allows a small tolerance for rounding differences.
        """
        adjustments_total = sum(a.amount for a in self.adjustments)
        expected_total = self.subtotal + adjustments_total + self.tax
        if abs(expected_total - self.total_amount) > 1.0:
            raise ValueError(
                f"Totals don't add up: subtotal ({self.subtotal}) + adjustments "
                f"({adjustments_total}) + tax ({self.tax}) = {expected_total}, but "
                f"total_amount is {self.total_amount}."
            )
        return self


# --- Extraction ---

def build_extraction_prompt(raw_text: str) -> tuple[str, str]:
    """
    Builds the system + user prompt that instructs the LLM to output
    ONLY valid JSON matching the Invoice schema — no prose, no markdown
    fences, just the raw JSON object.
    """
    system_prompt = (
        "You extract structured data from invoice/receipt text. "
        "Respond with ONLY a valid JSON object — no explanations, no markdown "
        "code fences, no extra text before or after. The JSON must match this "
        "exact structure:\n\n"
        "{\n"
        '  "vendor_name": string,\n'
        '  "invoice_number": string,\n'
        '  "invoice_date": string,\n'
        '  "billed_to": string,\n'
        '  "line_items": [\n'
        "    {\n"
        '      "description": string,\n'
        '      "quantity": number,\n'
        '      "unit_price": number,\n'
        '      "total": number\n'
        "    }\n"
        "  ],\n"
        '  "subtotal": number,\n'
        '  "adjustments": [\n'
        "    {\n"
        '      "label": string,\n'
        '      "amount": number\n'
        "    }\n"
        "  ],\n"
        '  "tax": number,\n'
        '  "total_amount": number\n'
        "}\n\n"
        "The \"adjustments\" array is for anything that changes the total besides "
        "tax and the line items themselves — discounts, shipping charges, rounding, "
        "service fees, etc. Use a clear label for each one. IMPORTANT sign convention: "
        "a discount (anything that REDUCES the total) must be a NEGATIVE number; a "
        "surcharge or fee (anything that INCREASES the total) must be a POSITIVE "
        "number. If there are no such adjustments, use an empty array [].\n\n"
        "If a field genuinely isn't present in the text, make a reasonable "
        "best guess from context, or use 0 for missing numeric fields."
    )
    user_prompt = f"Invoice text:\n\n{raw_text}"
    return system_prompt, user_prompt



# Matches the "-----\nBILL 001\n-----\n<content>" section pattern used in
# batch test files that bundle multiple bills into one text file.
MULTI_BILL_PATTERN = re.compile(
    r'-{5,}\s*\nBILL\s+\d+\s*\n-{5,}\s*\n(.*?)(?=\n{1,3}-{5,}\s*\nBILL\s+\d+\s*\n-{5,}\s*\n|\Z)',
    re.DOTALL | re.IGNORECASE
)


def split_multi_bill_text(raw_text: str) -> list:
    """
    Detects whether raw_text actually contains MULTIPLE bills bundled into
    one file (separated by 'BILL 001', 'BILL 002', ... section headers), and
    if so, splits it into a list of individual bill texts — one per section.

    If the pattern isn't found (a normal single-invoice file), returns the
    whole text as a single-item list, so nothing changes for ordinary files.
    """
    matches = MULTI_BILL_PATTERN.findall(raw_text)
    if len(matches) >= 2:   # only treat as a batch if we found more than one
        return [m.strip() for m in matches]
    return [raw_text]


def get_raw_text(file_path: str) -> str:
    """
    Returns the raw text content of a file, regardless of its format.
    - .pdf  -> uses extract_pages() (PyMuPDF) to pull text page-by-page
    - .txt  -> just reads the file directly, since it's already plain text
    Raises ValueError for unsupported file types.
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        pages = extract_pages(file_path)
        return "\n".join(text for _, text in pages)
    elif ext == ".txt":
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    else:
        raise ValueError(f"Unsupported file type: '{ext}'. Supported: .pdf, .txt")


def extract_invoice_from_text(raw_text: str) -> Invoice:
    """
    Core extraction step, working on plain text regardless of where it
    came from. Prompts the LLM for JSON, then validates it with Pydantic.
    """
    system_prompt, user_prompt = build_extraction_prompt(raw_text)

    response = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,  # very low — we want consistent, literal extraction, not creativity
    )

    raw_json = response.choices[0].message.content.strip()

    # Defensive: strip markdown fences if the model added them anyway
    if raw_json.startswith("```"):
        raw_json = raw_json.strip("`")
        if raw_json.startswith("json"):
            raw_json = raw_json[4:].strip()

    data = json.loads(raw_json)          # may raise json.JSONDecodeError
    return Invoice(**data)                # may raise pydantic.ValidationError


def extract_invoice(file_path: str) -> Invoice:
    """
    Full single-file pipeline: get raw text (format-aware) -> extract -> validate.
    """
    raw_text = get_raw_text(file_path)
    return extract_invoice_from_text(raw_text)


def process_files_stream(file_paths: list, max_workers: int = 5):
    """
    Like process_files(), but:
    1. Runs extractions CONCURRENTLY (a thread pool, since each call is
       mostly just waiting on the Groq API over the network) instead of
       one-by-one, so a 20-bill batch finishes much faster.
    2. YIELDS each result as soon as it's ready, instead of collecting
       everything before returning — so the caller (Flask) can stream
       progress back to the browser in real time.

    Yields dicts of the same shape as process_files(), plus "index" and
    "total" so the UI can show "6 of 20 processed".
    """
    # Step 1: build one flat task list of (label, bill_text) across all
    # files, splitting any multi-bill files first
    tasks = []
    for path in file_paths:
        filename = os.path.basename(path)
        try:
            raw_text = get_raw_text(path)
        except ValueError as e:
            tasks.append(("error", filename, "unsupported_format", str(e)))
            continue

        bill_texts = split_multi_bill_text(raw_text)
        is_batch_file = len(bill_texts) > 1
        for i, bill_text in enumerate(bill_texts, start=1):
            label = f"{filename} — Bill {i}" if is_batch_file else filename
            tasks.append(("pending", label, bill_text, None))

    total = len(tasks)
    completed = 0

    # Step 2: run all the actual extraction tasks concurrently
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_label = {}
        for task in tasks:
            if task[0] == "error":
                # Already-known failures (bad file type) don't need a thread
                completed += 1
                yield {
                    "filename": task[1], "status": "error",
                    "error_type": task[2], "message": task[3],
                    "index": completed, "total": total,
                }
                continue
            label, bill_text = task[1], task[2]
            future = executor.submit(extract_invoice_from_text, bill_text)
            future_to_label[future] = label

        for future in as_completed(future_to_label):
            label = future_to_label[future]
            completed += 1
            try:
                invoice = future.result()
                yield {
                    "filename": label, "status": "success", "invoice": invoice,
                    "index": completed, "total": total,
                }
            except json.JSONDecodeError as e:
                yield {
                    "filename": label, "status": "error",
                    "error_type": "invalid_json", "message": str(e),
                    "index": completed, "total": total,
                }
            except ValidationError as e:
                yield {
                    "filename": label, "status": "error",
                    "error_type": "validation_failed", "message": str(e),
                    "index": completed, "total": total,
                }


def process_files(file_paths: list) -> list:
    """
    Batch pipeline: for each file, gets its raw text, checks whether it's
    actually a BUNDLE of multiple bills (splits it if so), then runs
    extraction on each individual bill independently — so one bad bill
    doesn't stop the rest, and a 20-bill test file produces 20 results
    instead of one failed attempt to merge them into a single invoice.

    Returns a list of result dicts, each either a success or a labeled failure:
    {"filename": ..., "status": "success", "invoice": Invoice}
    {"filename": ..., "status": "error", "error_type": ..., "message": ...}
    """
    results = []
    for path in file_paths:
        filename = os.path.basename(path)

        try:
            raw_text = get_raw_text(path)
        except ValueError as e:
            results.append({
                "filename": filename, "status": "error",
                "error_type": "unsupported_format", "message": str(e),
            })
            continue

        bill_texts = split_multi_bill_text(raw_text)
        is_batch_file = len(bill_texts) > 1

        for i, bill_text in enumerate(bill_texts, start=1):
            label = f"{filename} — Bill {i}" if is_batch_file else filename
            try:
                invoice = extract_invoice_from_text(bill_text)
                results.append({
                    "filename": label,
                    "status": "success",
                    "invoice": invoice,
                })
            except json.JSONDecodeError as e:
                results.append({
                    "filename": label, "status": "error",
                    "error_type": "invalid_json", "message": str(e),
                })
            except ValidationError as e:
                results.append({
                    "filename": label, "status": "error",
                    "error_type": "validation_failed", "message": str(e),
                })
    return results


if __name__ == "__main__":
    files = ["sample_invoice.pdf", "messy_invoice.pdf"]
    results = process_files(files)

    for r in results:
        print(f"\n=== {r['filename']} ===")
        if r["status"] == "success":
            print("Extraction succeeded:\n")
            print(r["invoice"].model_dump_json(indent=2))
        else:
            print(f"Failed ({r['error_type']}):")
            print(r["message"])
