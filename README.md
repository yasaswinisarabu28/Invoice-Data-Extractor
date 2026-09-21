# 🧾 Invoice Data Extractor

An AI-powered web application that extracts structured invoice data from messy **PDF and TXT files** using **Groq LLMs**, validates the extracted financial information with **Pydantic**, and presents the results in a clean web interface.

The application is designed to handle real-world invoices and receipts where formatting may be inconsistent, while reducing incorrect totals through arithmetic validation and a correction pass.

---

## ✨ Features

- 📄 Upload **PDF and TXT** invoices/receipts
- 📦 Upload and process **multiple files at once**
- 🧩 Detect and split files containing multiple bills
- 🤖 Extract structured invoice information using **Groq**
- 🧠 Use an LLM prompt specifically designed to avoid hallucinating missing values
- ✅ Validate extracted data using **Pydantic**
- 🧮 Verify invoice arithmetic:
  - `subtotal + adjustments + tax = total`
- 🔄 Automatically request a corrected extraction when totals fail validation
- 🏷️ Support discounts, fees, shipping charges, and other adjustments
- ⏳ Retry Groq requests when rate limits (`429`) occur
- ⚡ Stream batch-processing results to the browser as they are completed
- 📊 Display vendor, invoice number, date, customer, line items, subtotal, adjustments, tax, and total
- 📥 Download extracted invoice data as a newly generated PDF
- 🌟 Responsive dark/gold interface with drag-and-drop upload
- 🛡️ Keep API credentials in `.env` rather than hard-coding them

---

## 🏗️ Project Architecture

```text
User
 │
 │ Upload PDF / TXT
 ▼
┌──────────────────────────┐
│ Flask Web Application    │
│ app.py                   │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│ File/Text Extraction     │
│ extract.py               │
│ PyMuPDF                  │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│ Multi-Bill Detection     │
│ split_multi_bill_text()  │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│ Groq LLM Extraction      │
│ extract_invoice.py       │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│ JSON Parsing              │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│ Pydantic Validation       │
│ Invoice / LineItem        │
│ / Adjustment models       │
└────────────┬─────────────┘
             │
       ┌─────┴─────┐
       │           │
     Valid       Invalid
       │           │
       │           ▼
       │    Correction Prompt
       │           │
       │           ▼
       │       Groq Retry
       │           │
       └─────┬─────┘
             ▼
┌──────────────────────────┐
│ NDJSON Streaming          │
│ Flask → Browser           │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│ Frontend                  │
│ HTML + CSS + JavaScript   │
└────────────┬─────────────┘
             │
             ▼
       Invoice Result
             │
             ▼
       Download PDF
```

---

## 📁 Project Structure

```text
invoice_webapp/
│
├── app.py
├── extract.py
├── extract_invoice.py
├── .env
├── .gitignore
│
├── templates/
│   └── index.html
│
├── static/
│   ├── script.js
│   └── style.css
│
├── uploaded_files/
│   └── ...
│
├── sample_invoice.pdf
├── messy_invoice.pdf
├── sample_receipt.txt
│
└── README.md
```

### File Responsibilities

| File | Purpose |
|---|---|
| `app.py` | Flask server, file upload endpoint, streaming responses |
| `extract.py` | Extracts text from PDF pages using PyMuPDF |
| `extract_invoice.py` | Main AI extraction, schema validation, arithmetic checking, multi-bill handling, and Groq retry logic |
| `templates/index.html` | Main web page |
| `static/script.js` | Upload handling, streamed result processing, result rendering, PDF download |
| `static/style.css` | User interface styling |
| `.env` | Stores the Groq API key locally |
| `.gitignore` | Prevents secrets and Python cache files from being committed |
| `uploaded_files/` | Temporary storage for uploaded files |

---

## 🔄 How the Application Works

### 1. Upload

The user can:

- Drag and drop files into the upload area
- Click the upload area and select files
- Upload multiple files simultaneously

Supported formats:

```text
.pdf
.txt
```

---

### 2. Text Extraction

For PDF files, the application uses **PyMuPDF** to extract text page by page.

```text
PDF
 ↓
PyMuPDF
 ↓
Page text
 ↓
Combined invoice text
```

For TXT files, the application reads the file directly using UTF-8 encoding.

> Note: The current implementation extracts text from PDFs but does not perform OCR on scanned/image-only PDFs.

---

### 3. Multi-Bill Detection

The application can identify specially formatted text files containing multiple bills.

For example:

```text
---------------------
BILL 1
---------------------
...

---------------------
BILL 2
---------------------
...
```

Each detected bill is processed separately.

---

### 4. AI Extraction

The invoice text is sent to the Groq API.

The model is instructed to return structured JSON containing:

```json
{
  "vendor_name": "Example Store",
  "invoice_number": "INV-001",
  "invoice_date": "2026-09-21",
  "billed_to": "Customer",
  "line_items": [
    {
      "description": "Product",
      "quantity": 2,
      "unit_price": 100,
      "total": 200
    }
  ],
  "subtotal": 200,
  "adjustments": [],
  "tax": 20,
  "total_amount": 220
}
```

The extraction prompt also tells the model not to invent:

- Discounts
- Fees
- Shipping charges
- Taxes
- Invoice numbers
- Dates
- Rounding adjustments
- Other unsupported values

Missing numeric fields are represented as `0`, while missing text fields are represented as `"UNKNOWN"`.

---

## 🧮 Financial Validation

The application uses Pydantic models to validate the extracted structure.

The main invoice rule is:

```text
subtotal + adjustments + tax = total_amount
```

For example:

```text
Subtotal       = 1000
Discount       = -100
Tax            = 90
-------------------
Total          = 990
```

The implementation allows a tolerance of `1.0` for small rounding differences.

If the extracted values do not satisfy the validation rule, the application sends the invoice text, previous JSON, and validation error back to Groq with a correction prompt.

The correction prompt specifically tells the model **not to invent an adjustment simply to make the arithmetic work**.

---

## 💰 Adjustments

Adjustments are represented separately from tax.

### Discount

A discount is negative:

```json
{
  "label": "Discount",
  "amount": -50
}
```

### Fee / Surcharge

A fee is positive:

```json
{
  "label": "Service Fee",
  "amount": 25
}
```

This makes the final calculation transparent:

```text
Subtotal
+ Adjustments
+ Tax
= Total
```

---

## 🚦 Rate-Limit Handling

Groq requests can return HTTP `429` when the API rate limit is reached.

The application includes retry handling.

The retry schedule is:

```text
Attempt 1 → wait 10 seconds
Attempt 2 → wait 20 seconds
Attempt 3 → wait 30 seconds
Attempt 4 → final attempt
```

This helps the application recover from temporary rate-limit errors without immediately failing the invoice.

---

## ⚡ Streaming Batch Processing

Instead of waiting until every uploaded invoice has finished processing, the backend streams individual JSON results to the browser.

The response uses:

```text
application/x-ndjson
```

Each completed invoice produces one JSON line.

This allows the UI to display results progressively:

```text
Uploading...
      ↓
Processed 1 of 5
      ↓
Processed 2 of 5
      ↓
Processed 3 of 5
      ↓
...
      ↓
Done
```

This is especially useful when several invoices are uploaded together.

---

## 🖥️ Frontend

The frontend is built with:

- HTML
- CSS
- Vanilla JavaScript
- jsPDF

### Interface Features

- Drag-and-drop upload
- Multiple-file selection
- Animated background
- Processing status
- Invoice result cards
- Line-item table
- Adjustment display
- Total calculation display
- Error display
- PDF download button

No frontend framework is required.

---

## 📥 Extracted Information

The application extracts the following information when available:

### Invoice Details

- Vendor name
- Invoice number
- Invoice date
- Billed-to/customer information

### Line Items

- Description
- Quantity
- Unit price
- Line-item total

### Financial Details

- Subtotal
- Discounts
- Fees
- Shipping/other adjustments
- Tax
- Final total

---

## 🛠️ Technologies Used

### Backend

- Python
- Flask
- Pydantic
- Groq API
- python-dotenv
- PyMuPDF

### Frontend

- HTML5
- CSS3
- JavaScript
- jsPDF

### AI

- Groq LLM API
- `openai/gpt-oss-120b`

---

## ⚙️ Installation

### 1. Clone the Repository

```bash
git clone <YOUR_GITHUB_REPOSITORY_URL>
cd invoice_webapp
```

---

### 2. Create a Virtual Environment

Windows:

```bash
python -m venv venv
```

Activate it:

```bash
venv\Scripts\activate
```

---

### 3. Install Dependencies

The project includes a `requirements.txt` file containing the required Python packages:

```txt
Flask
PyMuPDF
pydantic
groq
python-dotenv
```

Install all dependencies with:

```bash
pip install -r requirements.txt
```

---

### 4. Create the `.env` File

Create a file named:

```text
.env
```

Add your Groq API key:

```env
GROQ_API_KEY=your_groq_api_key_here
```

Never commit this file to GitHub.

The project `.gitignore` already excludes:

```text
.env
__pycache__/
*.pyc
```

---

## ▶️ Running the Application

Start the Flask server:

```bash
python app.py
```

The application runs on:

```text
http://127.0.0.1:5001
```

Open that address in your browser.

---

## 🔌 API Endpoint

### `GET /`

Returns the invoice extraction web interface.

---

### `POST /process`

Processes uploaded invoice files.

Request:

```text
multipart/form-data
```

Field:

```text
files
```

Example response format:

```json
{
  "filename": "invoice.pdf",
  "status": "success",
  "invoice": {
    "vendor_name": "Example Store",
    "invoice_number": "INV-1001",
    "invoice_date": "2026-09-21",
    "billed_to": "Customer",
    "line_items": [],
    "subtotal": 1000,
    "adjustments": [],
    "tax": 180,
    "total_amount": 1180
  },
  "index": 1,
  "total": 1
}
```

The endpoint streams multiple results as newline-delimited JSON.

---

## 🧪 Testing With Sample Files

The repository contains sample invoice/receipt files that can be used for testing.

Examples include:

```text
sample_invoice.pdf
messy_invoice.pdf
sample_receipt.txt
```

You can upload them directly through the web interface.

---

## 🧠 Core Extraction Pipeline

The main extraction function follows this process:

```text
Input file
   ↓
Extract raw text
   ↓
Detect multiple bills
   ↓
Build extraction prompt
   ↓
Groq LLM
   ↓
JSON response
   ↓
Parse JSON
   ↓
Pydantic validation
   ↓
Valid? ──────────────── Yes ──→ Return invoice
   │
   No
   ↓
Build correction prompt
   ↓
Groq LLM
   ↓
Corrected JSON
   ↓
Pydantic validation
   ↓
Return validated invoice
```

---

## 🔐 Security Notes

### API Key

The Groq API key must be stored in `.env`:

```env
GROQ_API_KEY=...
```

Do not hard-code it in Python files.

### Uploaded Files

Uploaded files are saved in:

```text
uploaded_files/
```

For a production deployment, consider:

- File-size limits
- Filename sanitization
- Authentication
- Automatic cleanup of uploaded files
- Secure cloud/object storage
- HTTPS
- Request validation
- Rate limiting

---

## ⚠️ Current Limitations

1. **OCR is not included**
   - Image-only/scanned PDFs may not produce extractable text.

2. **Internet connection is required**
   - Invoice extraction depends on the Groq API.

3. **LLM extraction is probabilistic**
   - Validation helps catch arithmetic inconsistencies, but extracted values should still be reviewed for important financial workflows.

4. **PDF generation is client-side**
   - The extracted result PDF is generated in the browser using jsPDF.

5. **Temporary upload storage**
   - Uploaded files are stored locally in `uploaded_files/`.

6. **Multi-bill detection depends on formatting**
   - The current detector expects the bill separators used by the supported batch-text format.

---

## 🚀 Possible Future Improvements

- 🔍 OCR support for scanned invoices
- 🧾 Automatic invoice field confidence scores
- 📸 Image invoice support
- 🏦 Currency detection
- 🌍 Multi-language invoice extraction
- 🗃️ Database storage for extracted invoices
- 🔎 Search and filter previous invoices
- 👤 User authentication
- 📊 Invoice analytics dashboard
- ☁️ Cloud storage
- 🧪 Automated unit and integration tests
- 🔒 Production-grade file validation and security
- 📤 Export to CSV/Excel/JSON
- 🧠 Human-in-the-loop review for low-confidence extractions

---

## 📌 Example Use Case

A user receives a messy invoice containing:

```text
ACME MART

Laptop Stand     2 × 750
USB Cable        3 × 250

Subtotal:        2250
Discount:        -100
Tax:              387
Total:           2537
```

Instead of manually entering the information, the user uploads the invoice.

The application:

```text
Messy Invoice
     ↓
Text Extraction
     ↓
AI Understanding
     ↓
Structured JSON
     ↓
Financial Validation
     ↓
Clean Invoice Card
     ↓
Downloadable PDF
```

---

## 🎯 Project Goal

The goal of the project is to transform **unstructured and messy invoice/receipt documents into reliable, structured financial data** with minimal manual data entry.

The combination of:

- LLM-based information extraction
- Structured Pydantic schemas
- Arithmetic validation
- Correction prompting
- Batch processing
- Streaming results

creates an end-to-end invoice understanding workflow rather than a simple text extraction tool.

---

## 👩‍💻 Author

**Sarabu Yasaswini**

B.Tech Computer Science & Engineering

---

## 📄 License

Add your preferred open-source license before publishing the project publicly.

For example:

```text
MIT License
```

if you decide to release the project under MIT.
