"""
Gemini extraction service.

Flow per document:
  1. classify()  — identify doc type (invoice / contract / etc.)
  2. extract()   — pull structured data using the matching schema
"""

import base64
import mimetypes
import google.generativeai as genai

from app.core.config import settings
from app.models.extraction import (
    DocumentClassification,
    InvoiceExtraction,
    QuotationExtraction,
    PurchaseOrderExtraction,
    ContractExtraction,
)

genai.configure(api_key=settings.gemini_api_key)

MODEL = "gemini-2.0-flash"

# Map document_type string → extraction schema class
EXTRACTION_SCHEMA = {
    "invoice": InvoiceExtraction,
    "quotation": QuotationExtraction,
    "purchase_order": PurchaseOrderExtraction,
    "contract": ContractExtraction,
    "nda": ContractExtraction,
    "service_agreement": ContractExtraction,
}


def _file_part(file_bytes: bytes, filename: str) -> dict:
    """Build an inline_data part Gemini can read."""
    mime, _ = mimetypes.guess_type(filename)
    if mime is None:
        mime = "application/octet-stream"
    return {
        "inline_data": {
            "mime_type": mime,
            "data": base64.b64encode(file_bytes).decode("utf-8"),
        }
    }


def classify_document(file_bytes: bytes, filename: str) -> DocumentClassification:
    """
    Pass 1: ask Gemini to identify the document type.
    Returns a DocumentClassification with type, confidence, and reasoning.
    """
    model = genai.GenerativeModel(MODEL)

    prompt = (
        "You are a document classification expert. "
        "Look at this document and classify it.\n\n"
        "Return ONLY valid JSON matching this schema exactly:\n"
        '{"document_type": "<one of: invoice, quotation, purchase_order, contract, nda, service_agreement, unknown>", '
        '"confidence": <float 0.0-1.0>, '
        '"reasoning": "<one sentence>"}'
    )

    response = model.generate_content(
        [prompt, _file_part(file_bytes, filename)],
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=DocumentClassification,
            temperature=0.0,
        ),
    )

    return DocumentClassification.model_validate_json(response.text)


def extract_document(
    file_bytes: bytes,
    filename: str,
    document_type: str,
) -> dict:
    """
    Pass 2: extract structured data from the document using the correct schema.
    Returns a plain dict ready to be stored in the DB / synced to Sheets.

    Rules enforced via prompt:
    - Dates → YYYY-MM-DD
    - Currency → ISO 4217 code (USD, INR, GBP …)
    - Monetary values → float (no symbols, no commas)
    - line_items → JSON string of structured objects
    - If a field is not present in the document, return null
    """
    schema_class = EXTRACTION_SCHEMA.get(document_type)
    if schema_class is None:
        # Unknown doc type — return empty dict
        return {}

    model = genai.GenerativeModel(MODEL)

    prompt = (
        f"You are a financial document data extraction expert.\n"
        f"Extract ALL fields from this {document_type.replace('_', ' ')} document.\n\n"
        "STRICT RULES — follow exactly:\n"
        "1. Dates must be in YYYY-MM-DD format only.\n"
        "2. All monetary amounts must be plain float numbers (no currency symbols, no commas). "
        "   Example: 1500.00 not '$1,500'.\n"
        "3. currency must be a 3-letter ISO 4217 code: USD, INR, GBP, EUR, etc.\n"
        "4. tax_rate / discount_percent must be the percentage number only. "
        "   Example: 18.0 means 18%.\n"
        "5. line_items must be a JSON string (a serialised list) where each item has:\n"
        '   {"description": str, "quantity": float, "unit_price": float, '
        '   "discount_percent": float|null, "tax_percent": float|null, "line_total": float}\n'
        "6. If a field cannot be found in the document, return null — never guess or fabricate.\n"
        "7. Extract every line item present — do not truncate.\n"
    )

    response = model.generate_content(
        [prompt, _file_part(file_bytes, filename)],
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=schema_class,
            temperature=0.0,
        ),
    )

    instance = schema_class.model_validate_json(response.text)
    return instance.model_dump()


def run_extraction_pipeline(file_bytes: bytes, filename: str) -> tuple[str, dict]:
    """
    Full pipeline: classify → extract.
    Returns (document_type, extracted_data_dict).
    """
    classification = classify_document(file_bytes, filename)
    doc_type = classification.document_type
    extracted = extract_document(file_bytes, filename, doc_type)
    return doc_type, extracted
