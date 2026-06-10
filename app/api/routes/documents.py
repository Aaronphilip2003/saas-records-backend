from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from app.core.auth import get_current_user
from app.core.supabase import supabase
from app.services.storage import upload_file
from app.models.documents import DocumentUploadResponse, DocumentStatusResponse

router = APIRouter(prefix="/documents", tags=["documents"])


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
        .insert(
            {
                "user_id": user["id"],
                "name": file.filename,
                "file_type": ext,
                "file_size": size,
                "file_url": file_url,
                "status": "uploaded",
                "metadata": {},
            }
        )
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
    extracted_data = doc.get("metadata") or None

    return DocumentStatusResponse(
        id=doc["id"],
        status=doc["status"],
        document_type=doc.get("document_type"),
        extracted_data=extracted_data if doc["status"] == "extracted" else None,
    )
