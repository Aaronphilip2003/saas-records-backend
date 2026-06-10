from pydantic import BaseModel
from typing import Any
from enum import Enum


class DocumentStatus(str, Enum):
    uploaded = "uploaded"
    processing = "processing"
    extracted = "extracted"
    reviewed = "reviewed"
    saved = "saved"


class DocumentType(str, Enum):
    invoice = "invoice"
    quotation = "quotation"
    purchase_order = "purchase_order"
    contract = "contract"
    nda = "nda"
    service_agreement = "service_agreement"
    unknown = "unknown"


class DocumentUploadResponse(BaseModel):
    id: str
    name: str
    file_url: str
    file_type: str
    file_size: int
    status: DocumentStatus


class DocumentStatusResponse(BaseModel):
    id: str
    status: DocumentStatus
    document_type: DocumentType | None = None
    extracted_data: dict[str, Any] | None = None
