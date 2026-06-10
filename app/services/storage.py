import uuid
from fastapi import UploadFile
from app.core.supabase import supabase
from app.core.config import settings

ALLOWED_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "image/png": "png",
    "image/jpeg": "jpg",
}
MAX_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB


async def upload_file(file: UploadFile, user_id: str) -> tuple[str, str, int]:
    """
    Upload file to Supabase Storage.
    Returns (file_url, file_extension, file_size_bytes).
    """
    if file.content_type not in ALLOWED_TYPES:
        raise ValueError(f"Unsupported file type: {file.content_type}")

    contents = await file.read()
    if len(contents) > MAX_SIZE_BYTES:
        raise ValueError("File exceeds 25 MB limit")

    ext = ALLOWED_TYPES[file.content_type]
    storage_path = f"{user_id}/{uuid.uuid4()}.{ext}"

    supabase.storage.from_(settings.storage_bucket).upload(
        path=storage_path,
        file=contents,
        file_options={"content-type": file.content_type},
    )

    public_url = supabase.storage.from_(settings.storage_bucket).get_public_url(storage_path)

    return public_url, ext, len(contents)


def download_file(file_url: str) -> bytes:
    """Download a file from Supabase Storage by its public URL path component."""
    # Extract the storage path from the full URL
    # URL format: .../storage/v1/object/public/<bucket>/<path>
    marker = f"/object/public/{settings.storage_bucket}/"
    path = file_url.split(marker)[-1]
    return supabase.storage.from_(settings.storage_bucket).download(path)
