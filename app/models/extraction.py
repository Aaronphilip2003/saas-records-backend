"""
Structured extraction schemas — designed for flat, calculation-friendly Google Sheets output.

Rules:
- All monetary values are float (never strings)
- All dates are ISO 8601 strings: YYYY-MM-DD (never ambiguous formats)
- All percentage/rate values are float (e.g. tax_rate = 18.0 means 18%)
- line_items is a JSON string of a list of structured objects (see LineItem)
- Every field is Optional so partial extraction is valid — never fail on missing data
- currency is always a 3-letter ISO 4217 code (USD, INR, GBP, etc.)
"""

from pydantic import BaseModel, Field
from typing import Optional
import json


# ─── Shared primitives ────────────────────────────────────────────────────────

class LineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    discount_percent: Optional[float] = None   # e.g. 10.0 = 10%
    tax_percent: Optional[float] = None        # e.g. 18.0 = 18%
    line_total: Optional[float] = None         # quantity * unit_price after discount


# ─── Invoice ──────────────────────────────────────────────────────────────────

class InvoiceExtraction(BaseModel):
    # Identifiers
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None          # YYYY-MM-DD
    due_date: Optional[str] = None              # YYYY-MM-DD
    purchase_order_reference: Optional[str] = None

    # Vendor (the party issuing the invoice)
    vendor_name: Optional[str] = None
    vendor_email: Optional[str] = None
    vendor_phone: Optional[str] = None
    vendor_address: Optional[str] = None
    vendor_tax_id: Optional[str] = None         # GST, VAT, EIN, etc.

    # Bill-to (the party being billed)
    bill_to_name: Optional[str] = None
    bill_to_address: Optional[str] = None
    bill_to_tax_id: Optional[str] = None

    # Line items (JSON string for Sheets; each item follows LineItem schema)
    line_items: Optional[str] = None           # JSON string

    # Financials — all float
    subtotal: Optional[float] = None
    discount_amount: Optional[float] = None
    tax_rate: Optional[float] = None           # e.g. 18.0
    tax_amount: Optional[float] = None
    shipping_amount: Optional[float] = None
    total_amount: Optional[float] = None
    amount_paid: Optional[float] = None
    amount_due: Optional[float] = None
    currency: Optional[str] = None             # ISO 4217

    # Terms
    payment_terms: Optional[str] = None        # e.g. "Net 30"
    bank_account: Optional[str] = None
    notes: Optional[str] = None


# ─── Quotation ────────────────────────────────────────────────────────────────

class QuotationExtraction(BaseModel):
    quote_number: Optional[str] = None
    quote_date: Optional[str] = None           # YYYY-MM-DD
    valid_until: Optional[str] = None          # YYYY-MM-DD

    vendor_name: Optional[str] = None
    vendor_email: Optional[str] = None
    vendor_phone: Optional[str] = None
    vendor_address: Optional[str] = None
    vendor_tax_id: Optional[str] = None

    client_name: Optional[str] = None
    client_address: Optional[str] = None

    line_items: Optional[str] = None           # JSON string

    subtotal: Optional[float] = None
    discount_amount: Optional[float] = None
    tax_rate: Optional[float] = None
    tax_amount: Optional[float] = None
    total_amount: Optional[float] = None
    currency: Optional[str] = None

    delivery_terms: Optional[str] = None
    payment_terms: Optional[str] = None
    notes: Optional[str] = None


# ─── Purchase Order ───────────────────────────────────────────────────────────

class PurchaseOrderExtraction(BaseModel):
    po_number: Optional[str] = None
    order_date: Optional[str] = None           # YYYY-MM-DD
    delivery_date: Optional[str] = None        # YYYY-MM-DD
    invoice_reference: Optional[str] = None

    buyer_name: Optional[str] = None
    buyer_address: Optional[str] = None
    buyer_tax_id: Optional[str] = None

    vendor_name: Optional[str] = None
    vendor_address: Optional[str] = None
    vendor_tax_id: Optional[str] = None
    vendor_email: Optional[str] = None

    line_items: Optional[str] = None           # JSON string

    subtotal: Optional[float] = None
    tax_rate: Optional[float] = None
    tax_amount: Optional[float] = None
    total_amount: Optional[float] = None
    currency: Optional[str] = None

    shipping_address: Optional[str] = None
    payment_terms: Optional[str] = None
    notes: Optional[str] = None


# ─── Contract / NDA / Service Agreement ──────────────────────────────────────

class ContractExtraction(BaseModel):
    contract_type: Optional[str] = None        # "NDA", "Service Agreement", "Lease", etc.
    contract_title: Optional[str] = None
    contract_reference: Optional[str] = None

    effective_date: Optional[str] = None       # YYYY-MM-DD
    expiry_date: Optional[str] = None          # YYYY-MM-DD
    signing_date: Optional[str] = None         # YYYY-MM-DD

    party_a_name: Optional[str] = None
    party_a_address: Optional[str] = None
    party_a_tax_id: Optional[str] = None

    party_b_name: Optional[str] = None
    party_b_address: Optional[str] = None
    party_b_tax_id: Optional[str] = None

    # Financials (if applicable)
    contract_value: Optional[float] = None
    currency: Optional[str] = None
    payment_schedule: Optional[str] = None

    # Key terms
    renewal_terms: Optional[str] = None
    notice_period_days: Optional[int] = None
    governing_law: Optional[str] = None
    key_obligations: Optional[str] = None      # Short plain-text summary
    termination_clause: Optional[str] = None
    confidentiality_clause: Optional[str] = None
    notes: Optional[str] = None


# ─── Classification result ────────────────────────────────────────────────────

class DocumentClassification(BaseModel):
    document_type: str = Field(
        description=(
            "One of: invoice, quotation, purchase_order, contract, nda, "
            "service_agreement, unknown"
        )
    )
    confidence: float = Field(description="Confidence score between 0.0 and 1.0")
    reasoning: str = Field(description="One sentence explaining the classification")


# ─── Helper ──────────────────────────────────────────────────────────────────

def line_items_to_json(items: list[LineItem]) -> str:
    return json.dumps([i.model_dump(exclude_none=False) for i in items])
