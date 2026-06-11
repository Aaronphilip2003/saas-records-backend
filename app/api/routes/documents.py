import logging
import traceback
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, status
from app.core.auth import get_current_user
from app.core.supabase import supabase
from app.services.storage import upload_file, download_file
from app.services.extractor import run_extraction_pipeline

logger = logging.getLogger(__name__)
from app.models.documents import DocumentUploadResponse, DocumentStatusResponse

router = APIRouter(prefix="/documents", tags=["documents"])


# ─── Upload ──────────────────────────────────────────────────────────────────

@router.post("/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    try:
        file_url, ext, size = await upload_file(file, user["id"])
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    record = (
        supabase.table("documents")
        .insert({
            "user_id": user["id"],
            "name": file.filename,
            "file_type": ext,
            "file_size": size,
            "file_url": file_url,
            "status": "uploaded",
            "metadata": {},
        })
        .execute()
    )

    doc = record.data[0]
    return DocumentUploadResponse(
        id=doc["id"],
        name=doc["name"],
        file_url=doc["file_url"],
        file_type=doc["file_type"],
        file_size=doc["file_size"],
        status=doc["status"],
    )


# ─── Extract (background task) ───────────────────────────────────────────────

RECORD_TYPE_MAP = {
    "invoice": "invoice",
    "quotation": "invoice",
    "purchase_order": "purchase_order",
    "contract": "contract",
    "nda": "contract",
    "service_agreement": "contract",
}


def _run_extraction(document_id: str, file_url: str, filename: str, user_id: str):
    """
    Background task — runs after the HTTP response is sent.
    1. Mark document as processing
    2. Download file from storage
    3. Run Gemini classification + extraction
    4. Store results in document metadata + business_records
    5. Mark document as saved
    """
    try:
        # 1. Mark processing
        supabase.table("documents").update({"status": "processing"}).eq("id", document_id).execute()

        # 2. Download
        file_bytes = download_file(file_url)

        # 3. Gemini pipeline
        doc_type, extracted_data = run_extraction_pipeline(file_bytes, filename)

        # 4. Save to business_records
        record_type = RECORD_TYPE_MAP.get(doc_type, "invoice")
        br = (
            supabase.table("business_records")
            .insert({
                "user_id": user_id,
                "record_type": record_type,
                "source_id": document_id,
                "source_table": "documents",
                "data": extracted_data,
                "sync_status": "unsynced",
            })
            .execute()
        )

        # 5. Update document with results and mark saved
        supabase.table("documents").update({
            "status": "saved",
            "document_type": doc_type,
            "metadata": extracted_data,
        }).eq("id", document_id).execute()

        # 6. Activity log
        supabase.table("activity_logs").insert({
            "user_id": user_id,
            "action": "document_extracted",
            "entity_type": "document",
            "entity_id": document_id,
            "details": {"document_type": doc_type, "record_id": br.data[0]["id"]},
        }).execute()

    except Exception as e:
        error_detail = traceback.format_exc()
        logger.error("Extraction failed for document %s:\n%s", document_id, error_detail)
        supabase.table("documents").update({
            "status": "error",
            "metadata": {"extraction_error": str(e), "traceback": error_detail},
        }).eq("id", document_id).execute()


@router.post("/{document_id}/extract", status_code=status.HTTP_202_ACCEPTED)
async def trigger_extraction(
    document_id: str,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    result = (
        supabase.table("documents")
        .select("id, status, file_url, name")
        .eq("id", document_id)
        .eq("user_id", user["id"])
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    doc = result.data

    if doc["status"] == "processing":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Extraction already in progress")

    if doc["status"] in ("extracted", "saved"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Document already extracted")

    background_tasks.add_task(
        _run_extraction,
        document_id=document_id,
        file_url=doc["file_url"],
        filename=doc["name"],
        user_id=user["id"],
    )

    return {"message": "Extraction started", "document_id": document_id}


# ─── Delete ──────────────────────────────────────────────────────────────────

@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    user: dict = Depends(get_current_user),
):
    # Fetch the document first to get file_url
    result = (
        supabase.table("documents")
        .select("id, file_url, status")
        .eq("id", document_id)
        .eq("user_id", user["id"])
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    doc = result.data

    # 1. Delete file from Storage
    if doc.get("file_url"):
        try:
            marker = f"/object/public/{settings.storage_bucket}/"
            storage_path = doc["file_url"].split(marker)[-1]
            supabase.storage.from_(settings.storage_bucket).remove([storage_path])
        except Exception:
            pass  # Don't block deletion if file is already gone

    # 2. Delete associated business_records
    supabase.table("business_records").delete().eq("source_id", document_id).execute()

    # 3. Delete the document row
    supabase.table("documents").delete().eq("id", document_id).eq("user_id", user["id"]).execute()


# ─── Status polling ───────────────────────────────────────────────────────────

@router.get("/{document_id}/status", response_model=DocumentStatusResponse)
async def get_document_status(
    document_id: str,
    user: dict = Depends(get_current_user),
):
    result = (
        supabase.table("documents")
        .select("id, status, document_type, metadata")
        .eq("id", document_id)
        .eq("user_id", user["id"])
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    doc = result.data
    metadata = doc.get("metadata") or {}
    return DocumentStatusResponse(
        id=doc["id"],
        status=doc["status"],
        document_type=doc.get("document_type"),
        extracted_data=metadata if doc["status"] in ("extracted", "saved") else None,
        error=metadata.get("extraction_error") if doc["status"] == "error" else None,
    )

