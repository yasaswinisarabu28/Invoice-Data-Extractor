import os
import json
import re
import time
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import BaseModel, ValidationError, model_validator
from groq import Groq, RateLimitError
from dotenv import load_dotenv

from extract import extract_pages  # reused from Project 1


load_dotenv()
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))


# ---------------------------------------------------------
# Schema
# ---------------------------------------------------------

class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    total: float


class Adjustment(BaseModel):
    label: str
    amount: float


class Invoice(BaseModel):
    vendor_name: str
    invoice_number: str
    invoice_date: str
    billed_to: str
    line_items: List[LineItem]
    subtotal: float
    adjustments: List[Adjustment] = []
    tax: Optional[float] = 0.0
    total_amount: float

    @model_validator(mode="after")
    def check_totals_add_up(self):
        """
        Custom validation:
        Checks whether the extracted invoice totals approximately
        add up correctly.
        """

        adjustments_total = sum(
            a.amount for a in self.adjustments
        )

        expected_total = (
            self.subtotal
            + adjustments_total
            + self.tax
        )

        if abs(expected_total - self.total_amount) > 1.0:
            raise ValueError(
                f"Totals don't add up: "
                f"subtotal ({self.subtotal}) + "
                f"adjustments ({adjustments_total}) + "
                f"tax ({self.tax}) = {expected_total}, "
                f"but total_amount is {self.total_amount}."
            )

        return self


# ---------------------------------------------------------
# Extraction Prompt
# ---------------------------------------------------------

def build_extraction_prompt(raw_text: str) -> tuple[str, str]:
    """
    Builds the system + user prompt that instructs the LLM
    to output ONLY valid JSON matching the Invoice schema.
    """

    system_prompt = (
        "You extract structured data from invoice/receipt text. "
        "Respond with ONLY a valid JSON object — no explanations, "
        "no markdown code fences, no extra text before or after. "
        "The JSON must match this exact structure:\n\n"

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

        "The \"adjustments\" array is for anything that changes "
        "the total besides tax and the line items themselves — "
        "discounts, shipping charges, rounding, service fees, etc. "

        "Use a clear label for each one. "

        "IMPORTANT sign convention: "
        "a discount (anything that REDUCES the total) must be "
        "a NEGATIVE number; a surcharge or fee (anything that "
        "INCREASES the total) must be a POSITIVE number. "

        "If there are no such adjustments, use an empty array [].\n\n"

        "IMPORTANT: Do NOT invent discounts, fees, shipping charges, "
"rounding adjustments, taxes, invoice numbers, dates, or other "
"values that are not explicitly supported by the invoice text. "

"If no discount, fee, shipping charge, rounding adjustment, "
"or other adjustment is explicitly shown, use an empty array []. "

"If a numeric field is genuinely missing, use 0. "
"For missing text fields, use 'UNKNOWN'. "

"Before returning the JSON, verify the arithmetic: "
"subtotal + adjustments + tax should equal total_amount. "
"Do not create an adjustment just to make the arithmetic work."
    )

    user_prompt = f"Invoice text:\n\n{raw_text}"

    return system_prompt, user_prompt


# ---------------------------------------------------------
# Multi-Bill Detection
# -------------------------------------------------------

MULTI_BILL_PATTERN = re.compile(
    r'-{5,}\s*\n'
    r'BILL\s+\d+\s*\n'
    r'-{5,}\s*\n'
    r'(.*?)(?='
    r'\n{1,3}-{5,}\s*\n'
    r'BILL\s+\d+\s*\n'
    r'-{5,}\s*\n'
    r'|\Z)',
    re.DOTALL | re.IGNORECASE
)


def split_multi_bill_text(raw_text: str) -> list:
    """
    Detects whether raw_text contains multiple bills bundled
    into one file.

    If multiple bills are found, each bill is returned separately.
    Otherwise the complete text is returned as one item.
    """

    matches = MULTI_BILL_PATTERN.findall(raw_text)

    if len(matches) >= 2:
        return [m.strip() for m in matches]

    return [raw_text]


# ---------------------------------------------------------
# Read Input File
# ---------------------------------------------------------

def get_raw_text(file_path: str) -> str:
    """
    Returns raw text from PDF or TXT files.

    PDF -> extracted using extract_pages()
    TXT -> read directly
    """

    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":

        pages = extract_pages(file_path)

        return "\n".join(
            text for _, text in pages
        )

    elif ext == ".txt":

        with open(
            file_path,
            "r",
            encoding="utf-8"
        ) as f:

            return f.read()

    else:

        raise ValueError(
            f"Unsupported file type: '{ext}'. "
            "Supported: .pdf, .txt"
        )


# ---------------------------------------------------------
# Groq Extraction With Retry
# ---------------------------------------------------------

def extract_invoice_from_text(raw_text: str) -> Invoice:
    """
    Sends invoice text to Groq and converts the response
    into a validated Invoice object.

    Includes automatic retry handling for Groq 429
    rate-limit errors.
    """

    system_prompt, user_prompt = build_extraction_prompt(
        raw_text
    )

    # Maximum number of attempts
    max_retries = 4

    response = None

    for attempt in range(max_retries):

        try:

            print(
                f"Sending invoice to Groq "
                f"(attempt {attempt + 1}/{max_retries})..."
            )

            response = groq_client.chat.completions.create(

                model="openai/gpt-oss-120b",

                messages=[
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": user_prompt
                    },
                ],

                temperature=0.1,
            )

            # Request succeeded
            break

        except RateLimitError as e:

            # If this was the final attempt,
            # allow the error to go to the caller.
            if attempt == max_retries - 1:

                print(
                    "Groq rate limit still active "
                    "after all retry attempts."
                )

                raise

            # Increasing wait time
            wait_time = 10 * (attempt + 1)

            print(
                f"Groq rate limit reached (429). "
                f"Waiting {wait_time} seconds before retry..."
            )

            time.sleep(wait_time)

        except Exception:
            # Other errors should not be treated
            # as rate-limit errors.
            raise

    # -----------------------------------------------------
    # Read model response
    # -----------------------------------------------------

    raw_json = response.choices[0].message.content.strip()

    # Defensive:
    # Remove markdown code fences if the model adds them.
    if raw_json.startswith("```"):

        raw_json = raw_json.strip("`")

        if raw_json.startswith("json"):

            raw_json = raw_json[4:].strip()

    data = json.loads(raw_json)

try:
    return Invoice(**data)

except ValidationError as e:
    print("Invoice validation failed.")
    print("Retrying extraction with arithmetic correction...")

    correction_prompt = f"""
The previous extraction contained an arithmetic inconsistency.

Original invoice text:
{raw_text}

Previous extracted JSON:
{json.dumps(data, indent=2)}

Validation error:
{str(e)}

Extract the invoice again.

IMPORTANT:
- Do NOT invent discounts, fees, shipping charges, rounding,
  or other adjustments.
- Only include an adjustment if it is explicitly present
  in the invoice text.
- If there is no explicit adjustment, use [].
- Check that:

  subtotal + adjustments + tax = total_amount

- Return ONLY valid JSON.
- Do not include explanations.
"""

    response = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": correction_prompt
            }
        ],
        temperature=0.0,
    )

    corrected_json = response.choices[0].message.content.strip()

    if corrected_json.startswith("```"):
        corrected_json = corrected_json.strip("`")

        if corrected_json.startswith("json"):
            corrected_json = corrected_json[4:].strip()

    corrected_data = json.loads(corrected_json)

    return Invoice(**corrected_data)

# ---------------------------------------------------------
# Single File Extraction
# ---------------------------------------------------------

def extract_invoice(file_path: str) -> Invoice:
    """
    Full single-file pipeline:

    File
      ↓
    Raw text
      ↓
    Groq
      ↓
    JSON
      ↓
    Pydantic validation
    """

    raw_text = get_raw_text(file_path)

    return extract_invoice_from_text(raw_text)


# ---------------------------------------------------------
# Streaming Batch Processing
# ---------------------------------------------------------

def process_files_stream(
    file_paths: list,
    max_workers: int = 1
):
    """
    Processes invoices and yields results as they finish.

    IMPORTANT:
    max_workers is set to 1 by default so that only one
    Groq request is sent at a time.

    This prevents multiple simultaneous requests from
    exceeding the Groq TPM rate limit.
    """

    # -----------------------------------------------------
    # Step 1:
    # Build flat task list
    # -----------------------------------------------------

    tasks = []

    for path in file_paths:

        filename = os.path.basename(path)

        try:

            raw_text = get_raw_text(path)

        except ValueError as e:

            tasks.append(
                (
                    "error",
                    filename,
                    "unsupported_format",
                    str(e)
                )
            )

            continue

        bill_texts = split_multi_bill_text(raw_text)

        is_batch_file = len(bill_texts) > 1

        for i, bill_text in enumerate(
            bill_texts,
            start=1
        ):

            if is_batch_file:

                label = (
                    f"{filename} — Bill {i}"
                )

            else:

                label = filename

            tasks.append(
                (
                    "pending",
                    label,
                    bill_text,
                    None
                )
            )

    total = len(tasks)

    completed = 0

    # -----------------------------------------------------
    # Step 2:
    # Run extraction tasks
    #
    # max_workers=1 prevents multiple Groq calls
    # from happening simultaneously.
    # -----------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=max_workers
    ) as executor:

        future_to_label = {}

        for task in tasks:

            # Already-known file errors
            if task[0] == "error":

                completed += 1

                yield {
                    "filename": task[1],
                    "status": "error",
                    "error_type": task[2],
                    "message": task[3],
                    "index": completed,
                    "total": total,
                }

                continue

            label = task[1]
            bill_text = task[2]

            future = executor.submit(
                extract_invoice_from_text,
                bill_text
            )

            future_to_label[future] = label

        # -------------------------------------------------
        # Process completed tasks
        # -------------------------------------------------

        for future in as_completed(
            future_to_label
        ):

            label = future_to_label[future]

            completed += 1

            try:

                invoice = future.result()

                yield {
                    "filename": label,
                    "status": "success",
                    "invoice": invoice,
                    "index": completed,
                    "total": total,
                }

            except json.JSONDecodeError as e:
                yield {
                    "filename": label,
                    "status": "error",
                    "error_type": "invalid_json",
                    "message": str(e),
                    "index": completed,
                    "total": total,
                }

            except ValidationError as e:

                yield {
                    "filename": label,
                    "status": "error",
                    "error_type": "validation_failed",
                    "message": str(e),
                    "index": completed,
                    "total": total,
                }

            except Exception as e:

                yield {
                    "filename": label,
                    "status": "error",
                    "error_type": "extraction_failed",
                    "message": str(e),
                    "index": completed,
                    "total": total,
                }


# ---------------------------------------------------------
# Normal Batch Processing
# ---------------------------------------------------------

def process_files(file_paths: list) -> list:
    """
    Batch pipeline.

    Processes each invoice one at a time.

    One failed invoice does not stop the remaining invoices.
    """

    results = []

    for path in file_paths:

        filename = os.path.basename(path)

        try:

            raw_text = get_raw_text(path)

        except ValueError as e:

            results.append({
                "filename": filename,
                "status": "error",
                "error_type": "unsupported_format",
                "message": str(e),
            })

            continue

        bill_texts = split_multi_bill_text(
            raw_text
        )

        is_batch_file = len(bill_texts) > 1

        for i, bill_text in enumerate(
            bill_texts,
            start=1
        ):

            if is_batch_file:

                label = (
                    f"{filename} — Bill {i}"
                )

            else:

                label = filename

            try:

                invoice = extract_invoice_from_text(
                    bill_text
                )

                results.append({
                    "filename": label,
                    "status": "success",
                    "invoice": invoice,
                })

            except json.JSONDecodeError as e:

                results.append({
                    "filename": label,
                    "status": "error",
                    "error_type": "invalid_json",
                    "message": str(e),
                })

            except ValidationError as e:

                results.append({
                    "filename": label,
                    "status": "error",
                    "error_type": "validation_failed",
                    "message": str(e),
                })

            except Exception as e:

                results.append({
                    "filename": label,
                    "status": "error",
                    "error_type": "extraction_failed",
                    "message": str(e),
                })

    return results


# ---------------------------------------------------------
# Test
# ---------------------------------------------------------

if __name__ == "__main__":

    files = [
        "sample_invoice.pdf",
        "messy_invoice.pdf"
    ]

    results = process_files(files)

    for r in results:

        print(
            f"\n=== {r['filename']} ==="
        )

        if r["status"] == "success":

            print(
                "Extraction succeeded:\n"
            )

            print(
                r["invoice"].model_dump_json(
                    indent=2
                )
            )

        else:

            print(
                f"Failed ({r['error_type']}):"
            )

            print(
                r["message"]
            )