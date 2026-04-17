"""Request/Response schemas for the Storage Service."""

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field


def to_camel(string: str) -> str:
    """Convert snake_case to camelCase."""
    components = string.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


class CamelModel(BaseModel):
    """Base model with camelCase serialization."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        alias_generator=to_camel,
    )


# ============================================================================
# Generic Response Models
# ============================================================================

T = TypeVar("T")


class PaginatedResponse(CamelModel, Generic[T]):
    """Generic paginated response."""

    items: list[T]
    total: int
    page: int
    page_size: int
    has_more: bool


class ErrorResponse(BaseModel):
    """Error response model."""

    error: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# Storage File Schemas
# ============================================================================


class StorageFileResponse(CamelModel):
    """Response schema for a storage file (GCS object)."""

    name: str
    size: int | None = None
    content_type: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    storage_class: str | None = None
    md5_hash: str | None = None
    public_url: str | None = None  # permanent public GCS URL


class StorageFileUploadResponse(CamelModel):
    """Response after uploading a file."""

    name: str
    size: int | None = None
    storage_id: str  # GCS object generation ID
    storage_link: str  # signed URL or public URL


class StorageFileLinkResponse(CamelModel):
    """Response with file links."""

    name: str
    signed_url: str
    expires_in_minutes: int
    public_url: str | None = None


# ============================================================================
# Search / List Schemas
# ============================================================================


class StorageSearchResponse(CamelModel):
    """Response from listing storage files."""

    files: list[StorageFileResponse]
    total: int
    next_page_token: str | None = None


# ============================================================================
# Quota / Stats Schemas
# ============================================================================


class StorageQuotaResponse(CamelModel):
    """Response with bucket information."""

    bucket_name: str
    location: str | None = None
    storage_class: str | None = None
    lifecycle_rules: list[dict[str, Any]] = Field(default_factory=list)


class StorageStatsResponse(CamelModel):
    """Storage usage statistics."""

    total_files: int = 0
    total_size_bytes: int = 0
    total_size_human: str = ""


# ============================================================================
# Batch Upload Schemas
# ============================================================================


class BatchUploadItem(CamelModel):
    """Single item in a batch upload result."""

    filename: str
    success: bool
    storage_id: str | None = None
    storage_link: str | None = None
    error: str | None = None
    existed: bool = False


class BatchUploadResponse(CamelModel):
    """Response from a batch upload."""

    total: int
    successful: int
    failed: int
    items: list[BatchUploadItem]


# ============================================================================
# Health Check Schemas
# ============================================================================


class DependencyStatus(CamelModel):
    """Health status of a single dependency."""

    status: str  # healthy | degraded | unhealthy
    latency_ms: float | None = None
    error: str | None = None


class HealthResponse(CamelModel):
    """Health check response."""

    status: str
    version: str
    environment: str
    bucket: str = ""
    dependencies: dict[str, DependencyStatus] = Field(default_factory=dict)
