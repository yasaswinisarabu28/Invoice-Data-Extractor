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


# =========================================================
# GROQ SETUP
# =========================================================

load_dotenv()

groq_client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)


# =========================================================
# SCHEMA
# =========================================================

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
        Check whether:

        subtotal + adjustments + tax = total_amount

        A tolerance of 1.0 is allowed for small
        rounding differences.
        """

        adjustments_total = sum(
            adjustment.amount
            for adjustment in self.adjustments
        )

        expected_total = (
            self.subtotal
            + adjustments_total
            + self.tax
        )

        if abs(
            expected_total - self.total_amount
        ) > 1.0:

            raise ValueError(
                f"Totals don't add up: "
                f"subtotal ({self.subtotal}) + "
                f"adjustments ({adjustments_total}) + "
                f"tax ({self.tax}) = {expected_total}, "
                f"but total_amount is "
                f"{self.total_amount}."
            )

        return self


# =========================================================
# EXTRACTION PROMPT
# =========================================================

def build_extraction_prompt(
    raw_text: str
) -> tuple[str, str]:

    """
    Builds the system and user prompts.

    The model is instructed to return ONLY JSON.
    """

    system_prompt = (

        "You extract structured data from "
        "invoice/receipt text. "

        "Respond with ONLY a valid JSON object — "
        "no explanations, no markdown code fences, "
        "no extra text before or after. "

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

        # -------------------------------------------------
        # Adjustment rules
        # -------------------------------------------------

        "The \"adjustments\" array is for anything "
        "that changes the total besides tax and the "
        "line items themselves — discounts, shipping "
        "charges, rounding, service fees, etc. "

        "Use a clear label for each one. "

        "IMPORTANT sign convention: "

        "a discount (anything that REDUCES the total) "
        "must be a NEGATIVE number; "

        "a surcharge or fee (anything that INCREASES "
        "the total) must be a POSITIVE number. "

        "If there are no such adjustments, use "
        "an empty array [].\n\n"

        # -------------------------------------------------
        # Do not hallucinate values
        # -------------------------------------------------

        "IMPORTANT: Do NOT invent discounts, fees, "
        "shipping charges, rounding adjustments, taxes, "
        "invoice numbers, dates, or other values that "
        "are not explicitly supported by the invoice text. "

        "If no discount, fee, shipping charge, rounding "
        "adjustment, or other adjustment is explicitly "
        "shown, use an empty array []. "

        "If a numeric field is genuinely missing, "
        "use 0. "

        "For missing text fields, use 'UNKNOWN'.\n\n"

        # -------------------------------------------------
        # Arithmetic
        # -------------------------------------------------

        "Before returning the JSON, verify the arithmetic: "

        "subtotal + adjustments + tax should equal "
        "total_amount. "

        "Do NOT create an adjustment just to make "
        "the arithmetic work. "

        "Preserve the actual subtotal and total shown "
        "on the invoice."
    )

    user_prompt = (
        f"Invoice text:\n\n{raw_text}"
    )

    return system_prompt, user_prompt


# =========================================================
# MULTI-BILL DETECTION
# =========================================================

MULTI_BILL_PATTERN = re.compile(

    r'-{5,}\s*\n'

    r'BILL\s+\d+\s*\n'

    r'-{5,}\s*\n'

    r'(.*?)'

    r'(?='

    r'\n{1,3}-{5,}\s*\n'

    r'BILL\s+\d+\s*\n'

    r'-{5,}\s*\n'

    r'|\Z)',

    re.DOTALL | re.IGNORECASE
)


def split_multi_bill_text(
    raw_text: str
) -> list:

    """
    Detects whether raw_text contains multiple bills.

    If multiple bills are found, each bill is returned
    separately.

    Otherwise the entire text is returned as one item.
    """

    matches = MULTI_BILL_PATTERN.findall(
        raw_text
    )

    if len(matches) >= 2:

        return [
            match.strip()
            for match in matches
        ]

    return [raw_text]


# =========================================================
# READ INPUT FILE
# =========================================================

def get_raw_text(
    file_path: str
) -> str:

    """
    Returns raw text from:

    .pdf -> PyMuPDF extraction
    .txt -> direct file reading
    """

    ext = os.path.splitext(
        file_path
    )[1].lower()

    # -----------------------------------------------------
    # PDF
    # -----------------------------------------------------

    if ext == ".pdf":

        pages = extract_pages(
            file_path
        )

        return "\n".join(
            text
            for _, text in pages
        )

    # -----------------------------------------------------
    # TXT
    # -----------------------------------------------------

    elif ext == ".txt":

        with open(
            file_path,
            "r",
            encoding="utf-8"
        ) as file:

            return file.read()

    # -----------------------------------------------------
    # Unsupported
    # -----------------------------------------------------

    else:

        raise ValueError(
            f"Unsupported file type: '{ext}'. "
            "Supported: .pdf, .txt"
        )


# =========================================================
# GROQ CALL WITH RATE-LIMIT RETRY
# =========================================================

def call_groq(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.1
):

    """
    Sends a request to Groq.

    If Groq returns HTTP 429, wait and retry.

    Retry schedule:

        attempt 1 -> wait 10 seconds
        attempt 2 -> wait 20 seconds
        attempt 3 -> wait 30 seconds
        attempt 4 -> final attempt
    """

    max_retries = 4

    for attempt in range(
        max_retries
    ):

        try:

            print(
                f"Sending request to Groq "
                f"(attempt {attempt + 1}/"
                f"{max_retries})..."
            )

            response = (
                groq_client
                .chat
                .completions
                .create(

                    model="openai/gpt-oss-120b",

                    messages=[
                        {
                            "role": "system",
                            "content": system_prompt
                        },
                        {
                            "role": "user",
                            "content": user_prompt
                        }
                    ],

                    temperature=temperature
                )
            )

            return response

        except RateLimitError:

            # ---------------------------------------------
            # Last attempt
            # ---------------------------------------------

            if attempt == max_retries - 1:

                print(
                    "Groq rate limit still active "
                    "after all retry attempts."
                )

                raise

            # ---------------------------------------------
            # Wait before retry
            # ---------------------------------------------

            wait_time = 10 * (
                attempt + 1
            )

            print(
                f"Groq rate limit reached (429). "
                f"Waiting {wait_time} seconds "
                f"before retry..."
            )

            time.sleep(
                wait_time
            )


# =========================================================
# CLEAN MODEL JSON
# =========================================================

def clean_json_response(
    raw_response: str
) -> str:

    """
    Removes markdown code fences if the model
    accidentally returns them.
    """

    raw_response = raw_response.strip()

    if raw_response.startswith("```"):

        raw_response = raw_response.strip(
            "`"
        )

        if raw_response.startswith(
            "json"
        ):

            raw_response = (
                raw_response[4:]
                .strip()
            )

    return raw_response


# =========================================================
# MAIN INVOICE EXTRACTION
# =========================================================

def extract_invoice_from_text(
    raw_text: str
) -> Invoice:

    """
    Main invoice extraction function.

    Flow:

        Invoice text
             ↓
        Groq extraction
             ↓
        JSON
             ↓
        Pydantic validation
             ↓
        ┌───────────────┐
        │               │
       valid          invalid
        │               │
        ↓               ↓
        ✅        Correction request
                         ↓
                       Groq
                         ↓
                    Validation
                         ↓
                         ✅
    """

    # -----------------------------------------------------
    # Build initial prompt
    # -----------------------------------------------------

    system_prompt, user_prompt = (
        build_extraction_prompt(
            raw_text
        )
    )

    # -----------------------------------------------------
    # FIRST GROQ REQUEST
    # -----------------------------------------------------

    response = call_groq(
        system_prompt,
        user_prompt,
        temperature=0.1
    )

    # -----------------------------------------------------
    # Read response
    # -----------------------------------------------------

    raw_json = (
        response
        .choices[0]
        .message
        .content
        .strip()
    )

    raw_json = clean_json_response(
        raw_json
    )

    # -----------------------------------------------------
    # Convert to Python dictionary
    # -----------------------------------------------------

    data = json.loads(
        raw_json
    )

    # -----------------------------------------------------
    # FIRST VALIDATION
    # -----------------------------------------------------

    try:

        invoice = Invoice(
            **data
        )

        return invoice

    except ValidationError as validation_error:

        print()
        print(
            "Invoice validation failed."
        )

        print(
            "Retrying extraction with "
            "arithmetic correction..."
        )

        # -------------------------------------------------
        # CORRECTION PROMPT
        # -------------------------------------------------

        correction_prompt = f"""

The previous extraction of this invoice
contained an arithmetic inconsistency.

ORIGINAL INVOICE TEXT:
{raw_text}


PREVIOUS EXTRACTED JSON:
{json.dumps(
    data,
    indent=2
)}


VALIDATION ERROR:
{str(validation_error)}


Extract the invoice again.

IMPORTANT RULES:

1. Do NOT invent discounts.

2. Do NOT invent fees.

3. Do NOT invent shipping charges.

4. Do NOT invent rounding adjustments.

5. Do NOT invent taxes.

6. Only include an adjustment if it is
   explicitly present in the original
   invoice text.

7. If there is no explicit adjustment,
   use:

   "adjustments": []

8. Do NOT create an adjustment simply
   to make the arithmetic work.

9. Preserve the actual subtotal shown
   on the invoice.

10. Preserve the actual total shown
    on the invoice.

11. Preserve the actual tax shown
    on the invoice.

12. Check:

    subtotal
    + adjustments
    + tax
    =
    total_amount

13. If the invoice has a rounding
    difference, include it ONLY if
    the rounding amount is explicitly
    shown.

14. If a numeric field is genuinely
    missing, use 0.

15. If a text field is genuinely
    missing, use "UNKNOWN".

16. Return ONLY valid JSON.

17. Do NOT return markdown.

18. Do NOT return explanations.

Return the corrected JSON now.
"""

        # -------------------------------------------------
        # SECOND GROQ REQUEST
        #
        # IMPORTANT:
        # This also uses call_groq(), so it also
        # gets 429 retry protection.
        # -------------------------------------------------

        correction_response = call_groq(
            system_prompt,
            correction_prompt,
            temperature=0.0
        )

        # -------------------------------------------------
        # Read corrected response
        # -------------------------------------------------

        corrected_json = (
            correction_response
            .choices[0]
            .message
            .content
            .strip()
        )

        corrected_json = clean_json_response(
            corrected_json
        )

        # -------------------------------------------------
        # Convert corrected JSON
        # -------------------------------------------------

        corrected_data = json.loads(
            corrected_json
        )

        # -------------------------------------------------
        # Final validation
        # -------------------------------------------------

        return Invoice(
            **corrected_data
        )


# =========================================================
# SINGLE FILE EXTRACTION
# =========================================================

def extract_invoice(
    file_path: str
) -> Invoice:

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
        Pydantic
          ↓
        Invoice
    """

    raw_text = get_raw_text(
        file_path
    )

    return extract_invoice_from_text(
        raw_text
    )


# =========================================================
# STREAMING BATCH PROCESSING
# =========================================================

def process_files_stream(
    file_paths: list,
    max_workers: int = 1
):

    """
    Processes invoices and yields results
    as they finish.

    max_workers=1 is intentional.

    This prevents several Groq requests from
    being sent simultaneously and reduces
    the chance of hitting the TPM rate limit.
    """

    # -----------------------------------------------------
    # STEP 1:
    # Build task list
    # -----------------------------------------------------

    tasks = []

    for path in file_paths:

        filename = os.path.basename(
            path
        )

        try:

            raw_text = get_raw_text(
                path
            )

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

        # ---------------------------------------------
        # Split multi-bill files
        # ---------------------------------------------

        bill_texts = split_multi_bill_text(
            raw_text
        )

        is_batch_file = (
            len(bill_texts) > 1
        )

        # ---------------------------------------------
        # Create individual tasks
        # ---------------------------------------------

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
    # STEP 2:
    # Run tasks
    # -----------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=max_workers
    ) as executor:

        future_to_label = {}

        for task in tasks:

            # ---------------------------------------------
            # Already-known error
            # ---------------------------------------------

            if task[0] == "error":

                completed += 1

                yield {
                    "filename": task[1],
                    "status": "error",
                    "error_type": task[2],
                    "message": task[3],
                    "index": completed,
                    "total": total
                }

                continue

            label = task[1]

            bill_text = task[2]

            future = executor.submit(
                extract_invoice_from_text,
                bill_text
            )

            future_to_label[
                future
            ] = label

        # -------------------------------------------------
        # Process completed tasks
        # -------------------------------------------------

        for future in as_completed(
            future_to_label
        ):

            label = future_to_label[
                future
            ]

            completed += 1

            try:

                invoice = future.result()

                yield {
                    "filename": label,
                    "status": "success",
                    "invoice": invoice,
                    "index": completed,
                    "total": total
                }

            # ---------------------------------------------
            # Invalid JSON
            # ---------------------------------------------

            except json.JSONDecodeError as e:

                yield {
                    "filename": label,
                    "status": "error",
                    "error_type": "invalid_json",
                    "message": str(e),
                    "index": completed,
                    "total": total
                }

            # ---------------------------------------------
            # Pydantic validation
            # ---------------------------------------------

            except ValidationError as e:

                yield {
                    "filename": label,
                    "status": "error",
                    "error_type": "validation_failed",
                    "message": str(e),
                    "index": completed,
                    "total": total
                }

            # ---------------------------------------------
            # Other errors
            # ---------------------------------------------

            except Exception as e:

                yield {
                    "filename": label,
                    "status": "error",
                    "error_type": "extraction_failed",
                    "message": str(e),
                    "index": completed,
                    "total": total
                }


# =========================================================
# NORMAL BATCH PROCESSING
# =========================================================

def process_files(
    file_paths: list
) -> list:

    """
    Processes files one by one.

    One failed invoice does not stop
    the remaining invoices.
    """

    results = []

    # -----------------------------------------------------
    # Process each file
    # -----------------------------------------------------

    for path in file_paths:

        filename = os.path.basename(
            path
        )

        try:

            raw_text = get_raw_text(
                path
            )

        except ValueError as e:

            results.append(
                {
                    "filename": filename,
                    "status": "error",
                    "error_type":
                        "unsupported_format",
                    "message": str(e)
                }
            )

            continue

        # -------------------------------------------------
        # Split multi-bill file
        # -------------------------------------------------

        bill_texts = split_multi_bill_text(
            raw_text
        )

        is_batch_file = (
            len(bill_texts) > 1
        )

        # -------------------------------------------------
        # Process each bill
        # -------------------------------------------------

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

                invoice = (
                    extract_invoice_from_text(
                        bill_text
                    )
                )

                results.append(
                    {
                        "filename": label,
                        "status": "success",
                        "invoice": invoice
                    }
                )

            except json.JSONDecodeError as e:

                results.append(
                    {
                        "filename": label,
                        "status": "error",
                        "error_type":
                            "invalid_json",
                        "message": str(e)
                    }
                )

            except ValidationError as e:

                results.append(
                    {
                        "filename": label,
                        "status": "error",
                        "error_type":
                            "validation_failed",
                        "message": str(e)
                    }
                )

            except Exception as e:

                results.append(
                    {
                        "filename": label,
                        "status": "error",
                        "error_type":
                            "extraction_failed",
                        "message": str(e)
                    }
                )

    return results


# =========================================================
# DIRECT TEST
# =========================================================

if __name__ == "__main__":

    files = [
        "sample_invoice.pdf",
        "messy_invoice.pdf"
    ]

    results = process_files(
        files
    )

    for result in results:

        print(
            f"\n=== {result['filename']} ==="
        )

        if result["status"] == "success":

            print(
                "Extraction succeeded:\n"
            )

            print(
                result["invoice"]
                .model_dump_json(
                    indent=2
                )
            )

        else:

            print(
                f"Failed "
                f"({result['error_type']}):"
            )

            print(
                result["message"]
            )