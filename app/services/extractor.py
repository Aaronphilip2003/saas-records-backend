"""
Document extraction service.

Pipeline:
  PDF  → pdfplumber (text extraction) → Groq LLM → structured JSON
  IMG  → Mistral OCR API (text extraction) → Groq LLM → structured JSON
"""

import io
import json
import base64
import pdfplumber
from groq import Groq
from mistralai import Mistral

from app.core.config import settings
from app.models.extraction import (
    DocumentClassification,
    InvoiceExtraction,
    QuotationExtraction,
    PurchaseOrderExtraction,
    ContractExtraction,
)

groq_client = Groq(api_key=settings.groq_api_key)
mistral_client = Mistral(api_key=settings.mistral_api_key)

GROQ_MODEL = "llama-3.3-70b-versatile"

EXTRACTION_SCHEMA = {
    "invoice": InvoiceExtraction,
    "quotation": QuotationExtraction,
    "purchase_order": PurchaseOrderExtraction,
    "contract": ContractExtraction,
    "nda": ContractExtraction,
    "service_agreement": ContractExtraction,
}


# ─── Text extraction ──────────────────────────────────────────────────────────

def _extract_text_from_pdf(file_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    text = "\n\n".join(pages).strip()
    if not text:
        raise ValueError("PDF appears to be scanned/image-based — no text could be extracted")
    return text


def _extract_text_from_image(file_bytes: bytes, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower()
    mime = "image/png" if ext == "png" else "image/jpeg"
    b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
    data_url = f"data:{mime};base64,{b64}"

    response = mistral_client.chat.complete(
        model="pixtral-12b-2409",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Extract ALL text from this document image exactly as it appears. "
                            "Preserve all numbers, dates, names, addresses, and line items. "
                            "Return only the raw extracted text, no commentary."
                        ),
                    },
                    {"type": "image_url", "image_url": data_url},
                ],
            }
        ],
    )
    return response.choices[0].message.content.strip()


def extract_text(file_bytes: bytes, filename: str) -> str:
    """Route to the correct text extractor based on file type."""
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext == "pdf":
        return _extract_text_from_pdf(file_bytes)
    elif ext in ("png", "jpg", "jpeg"):
        return _extract_text_from_image(file_bytes, filename)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


# ─── Classification ───────────────────────────────────────────────────────────

def classify_document(text: str) -> DocumentClassification:
    schema = DocumentClassification.model_json_schema()
    prompt = (
        "You are a document classification expert.\n"
        "Classify the following document text and return JSON matching this schema exactly:\n"
        f"{json.dumps(schema)}\n\n"
        "document_type must be one of: invoice, quotation, purchase_order, contract, nda, service_agreement, unknown\n\n"
        f"Document text:\n{text[:4000]}"
    )

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.0,
    )

    return DocumentClassification.model_validate_json(response.choices[0].message.content)


# ─── Extraction ───────────────────────────────────────────────────────────────

def extract_document(text: str, document_type: str) -> dict:
    schema_class = EXTRACTION_SCHEMA.get(document_type)
    if schema_class is None:
        return {}

    schema = schema_class.model_json_schema()
    prompt = (
        f"You are a financial document data extraction expert.\n"
        f"Extract ALL fields from this {document_type.replace('_', ' ')} and return JSON matching this schema:\n"
        f"{json.dumps(schema)}\n\n"
        "STRICT RULES:\n"
        "1. Dates must be YYYY-MM-DD format only.\n"
        "2. Monetary amounts must be plain float numbers — no currency symbols, no commas. Example: 1500.00\n"
        "3. currency must be a 3-letter ISO 4217 code: USD, INR, GBP, EUR, etc.\n"
        "4. tax_rate and discount_percent are percentage numbers only. Example: 18.0 means 18%.\n"
        "5. line_items must be a JSON string (serialised list) where each item has:\n"
        '   {"description": str, "quantity": float, "unit_price": float, '
        '   "discount_percent": float|null, "tax_percent": float|null, "line_total": float}\n'
        "6. If a field cannot be found, return null — never guess or fabricate.\n"
        "7. Extract every line item — do not truncate.\n\n"
        f"Document text:\n{text}"
    )

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.0,
    )

    instance = schema_class.model_validate_json(response.choices[0].message.content)
    return instance.model_dump()


# ─── Full pipeline ────────────────────────────────────────────────────────────

def run_extraction_pipeline(file_bytes: bytes, filename: str) -> tuple[str, dict]:
    """
    Full pipeline: extract text → classify → extract structured data.
    Returns (document_type, extracted_data_dict).
    """
    text = extract_text(file_bytes, filename)
    classification = classify_document(text)
    doc_type = classification.document_type
    extracted = extract_document(text, doc_type)
    return doc_type, extracted
