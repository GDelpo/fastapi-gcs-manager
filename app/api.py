"""Storage Service REST API endpoints."""

import mimetypes
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, File, Form, Query, Response, UploadFile

from app.config import settings
from app.dependencies import AdminUser, CurrentUser, ServiceUser, StorageServiceDep
from app.schemas import (
    BatchUploadResponse,
    StorageFileResponse,
    StorageFileLinkResponse,
    StorageFileUploadResponse,
    StorageQuotaResponse,
    StorageSearchResponse,
    StorageStatsResponse,
)

router = APIRouter(prefix="/api/v1", tags=["storage"])


# ============================================================================
# File Operations
# ============================================================================


@router.post(
    "/files",
    response_model=StorageFileUploadResponse,
    summary="Upload a file",
    description="Upload a single file to the storage bucket. "
    "Use skip_existing=true to avoid re-uploading duplicates.",
)
async def upload_file(
    service: StorageServiceDep,
    user: ServiceUser,
    file: UploadFile = File(...),
    custom_name: str | None = Form(None),
    skip_existing: bool = Form(False),
):
    """Upload a file to the storage bucket."""
    object_name = custom_name or file.filename
    content_type = file.content_type or mimetypes.guess_type(object_name)[0] or "application/octet-stream"
    content = await file.read()

    return await service.upload_file_from_bytes(
        data=content,
        object_name=object_name,
        content_type=content_type,
        skip_existing=skip_existing,
    )


@router.post(
    "/files/batch",
    response_model=BatchUploadResponse,
    summary="Batch upload files",
)
async def batch_upload(
    service: StorageServiceDep,
    user: ServiceUser,
    files: list[UploadFile] = File(...),
    skip_existing: bool = Form(True),
):
    """Upload multiple files to the storage bucket."""
    items = []
    successful = 0
    failed = 0

    for upload_file_item in files:
        object_name = upload_file_item.filename
        content_type = upload_file_item.content_type or "application/octet-stream"
        try:
            content = await upload_file_item.read()
            result = await service.upload_file_from_bytes(
                data=content,
                object_name=object_name,
                content_type=content_type,
                skip_existing=skip_existing,
            )
            from app.schemas import BatchUploadItem

            items.append(BatchUploadItem(
                filename=object_name,
                success=True,
                storage_id=result.storage_id,
                storage_link=result.storage_link,
            ))
            successful += 1
        except Exception as e:
            from app.schemas import BatchUploadItem

            items.append(BatchUploadItem(
                filename=object_name,
                success=False,
                error=str(e),
            ))
            failed += 1

    return BatchUploadResponse(
        total=len(files),
        successful=successful,
        failed=failed,
        items=items,
    )


@router.get(
    "/files",
    response_model=StorageSearchResponse,
    summary="List files",
    description="List files in the bucket with optional prefix filter.",
)
async def list_files(
    service: StorageServiceDep,
    user: CurrentUser,
    prefix: str | None = Query(None, description="Filter by object name prefix"),
    page_size: int = Query(100, ge=1, le=1000),
    page_token: str | None = Query(None, description="Pagination token"),
):
    """List files in the storage bucket."""
    return await service.list_files(
        prefix=prefix,
        max_results=page_size,
        page_token=page_token,
    )


@router.get(
    "/files/{object_name:path}/link",
    response_model=StorageFileLinkResponse,
    summary="Get signed URL",
    description="Generate a signed URL for temporary file access. "
    "Use expiration_minutes to control validity period.",
)
async def get_file_link(
    object_name: str,
    service: StorageServiceDep,
    user: CurrentUser,
    expiration_minutes: int = Query(
        None,
        ge=1,
        le=10080,
        description="URL expiration in minutes (default: config value, max: 7 days)",
    ),
):
    """Generate a signed URL for a file."""
    signed_url = await service.generate_signed_url(object_name, expiration_minutes)
    public_url = service.generate_public_url(object_name)
    return StorageFileLinkResponse(
        name=object_name,
        signed_url=signed_url,
        expires_in_minutes=expiration_minutes or settings.gcs_signed_url_expiration,
        public_url=public_url,
    )


@router.get(
    "/files/{object_name:path}/download",
    summary="Download file",
    description="Download file content directly.",
)
async def download_file(
    object_name: str,
    service: StorageServiceDep,
    user: CurrentUser,
):
    """Download a file from the storage bucket."""
    content, content_type = await service.download_file(object_name)
    filename = Path(object_name).name
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/files/{object_name:path}",
    response_model=StorageFileResponse,
    summary="Get file metadata",
)
async def get_file(
    object_name: str,
    service: StorageServiceDep,
    user: CurrentUser,
):
    """Get metadata for a specific file."""
    return await service.get_file_metadata(object_name)


@router.delete(
    "/files/{object_name:path}",
    summary="Delete file",
)
async def delete_file(
    object_name: str,
    service: StorageServiceDep,
    user: AdminUser,
):
    """Delete a file from the storage bucket."""
    await service.delete_file(object_name)
    return {"deleted": True, "name": object_name}


# ============================================================================
# Bucket Info & Stats
# ============================================================================


@router.get(
    "/quota",
    response_model=StorageQuotaResponse,
    summary="Get bucket info",
)
async def get_quota(
    service: StorageServiceDep,
    user: CurrentUser,
):
    """Get bucket metadata and lifecycle rules."""
    return await service.get_bucket_info()


@router.get(
    "/stats",
    response_model=StorageStatsResponse,
    summary="Get storage statistics",
    description="Returns file count and total size. Cached for 5 minutes.",
)
async def get_stats(
    service: StorageServiceDep,
    user: CurrentUser,
):
    """Get storage statistics (cached)."""
    return await service.get_stats()
