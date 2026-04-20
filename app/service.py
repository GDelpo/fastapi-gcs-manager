"""Google Cloud Storage Service - Core business logic.

Uses the ``google-cloud-storage`` library which is thread-safe and
does NOT require the serialisation lock needed by httplib2 in the
Drive service.
"""

import asyncio
import mimetypes
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from google.cloud import storage as gcs
from google.cloud.exceptions import GoogleCloudError, NotFound
from google.oauth2 import service_account

from app.config import settings
from app.exceptions import EntityNotFoundError, FileUploadError, QuotaExceededError, StorageAPIError
from app.logger import get_logger
from app.schemas import (
    BatchUploadItem,
    BatchUploadResponse,
    StorageFileResponse,
    StorageFileLinkResponse,
    StorageFileUploadResponse,
    StorageQuotaResponse,
    StorageSearchResponse,
    StorageStatsResponse,
)

logger = get_logger(__name__)

# In-memory cache for stats (avoid listing 500k+ objects on every request)
_stats_cache: dict = {"data": None, "ts": 0}
_STATS_TTL = 1800  # 30 minutes — scanning 500k+ objects is expensive
_stats_lock = asyncio.Lock()  # module-level lock is safe on Python 3.10+


def _humanize_bytes(n: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


class StorageService:
    """Service for Google Cloud Storage operations.

    Thread-safe — the ``google-cloud-storage`` client handles its own
    connection pooling and retry logic internally.
    """

    def __init__(self):
        self._client: gcs.Client | None = None
        self._bucket: gcs.Bucket | None = None

    @property
    def client(self) -> gcs.Client:
        """Lazy initialization of GCS client."""
        if self._client is None:
            self._client = self._create_client()
        return self._client

    @property
    def bucket(self) -> gcs.Bucket:
        """Lazy initialization of GCS bucket."""
        if self._bucket is None:
            self._bucket = self.client.bucket(settings.gcs_bucket_name)
        return self._bucket

    def _create_client(self) -> gcs.Client:
        """Create and authenticate GCS client using service account."""
        try:
            credentials = service_account.Credentials.from_service_account_file(
                settings.gcs_credentials_path,
            )
            client = gcs.Client(
                project=settings.gcs_project_id or credentials.project_id,
                credentials=credentials,
            )
            logger.info(
                "✅ GCS client initialized (project=%s, bucket=%s)",
                client.project,
                settings.gcs_bucket_name,
            )
            return client
        except Exception as e:
            logger.error("❌ Failed to initialize GCS client: %s", e)
            raise StorageAPIError(str(e), operation="init")

    # =========================================================================
    # Connection test
    # =========================================================================

    async def test_connection(self) -> bool:
        """Test connectivity to the GCS bucket."""
        try:
            bucket = await asyncio.to_thread(self.client.get_bucket, settings.gcs_bucket_name)
            return bucket.exists()
        except Exception as e:
            logger.warning("⚠️ GCS connection test failed: %s", e)
            return False

    # =========================================================================
    # File Operations
    # =========================================================================

    async def upload_file(
        self,
        local_path: str,
        object_name: str | None = None,
        content_type: str | None = None,
        *,
        skip_existing: bool = False,
    ) -> StorageFileUploadResponse:
        """Upload a file to the bucket.

        Args:
            local_path: Path to the file on the local filesystem.
            object_name: Object name in the bucket (defaults to filename).
            content_type: MIME type (auto-detected if not provided).
            skip_existing: If True, return existing object instead of re-uploading.

        Returns:
            Upload response with storage_id and signed URL.
        """
        path = Path(local_path)
        if not path.exists():
            raise FileUploadError(f"File not found: {local_path}", filename=path.name)

        if object_name is None:
            object_name = path.name

        if content_type is None:
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

        blob = self.bucket.blob(object_name)

        if skip_existing:
            exists = await asyncio.to_thread(blob.exists)
            if exists:
                await asyncio.to_thread(blob.reload)
                logger.info("⏭️ File already exists, skipping: %s", object_name)
                return StorageFileUploadResponse(
                    name=object_name,
                    size=blob.size,
                    storage_id=str(blob.generation),
                    storage_link=self.generate_public_url(object_name),
                )

        try:
            await asyncio.to_thread(
                blob.upload_from_filename,
                local_path,
                content_type=content_type,
            )
            logger.info("✅ File uploaded: %s (%s)", object_name, _humanize_bytes(blob.size or 0))
            return StorageFileUploadResponse(
                name=object_name,
                size=blob.size,
                storage_id=str(blob.generation),
                storage_link=self.generate_public_url(object_name),
            )
        except GoogleCloudError as e:
            logger.error("❌ Upload failed for %s: %s", object_name, e)
            raise FileUploadError(str(e), filename=object_name)

    async def upload_file_from_bytes(
        self,
        data: bytes | BinaryIO,
        object_name: str,
        content_type: str = "application/octet-stream",
        *,
        skip_existing: bool = False,
    ) -> StorageFileUploadResponse:
        """Upload file content from bytes or file-like object."""
        blob = self.bucket.blob(object_name)

        if skip_existing:
            exists = await asyncio.to_thread(blob.exists)
            if exists:
                await asyncio.to_thread(blob.reload)
                return StorageFileUploadResponse(
                    name=object_name,
                    size=blob.size,
                    storage_id=str(blob.generation),
                    storage_link=self.generate_public_url(object_name),
                )

        try:
            if isinstance(data, bytes):
                await asyncio.to_thread(
                    blob.upload_from_string,
                    data,
                    content_type=content_type,
                )
            else:
                await asyncio.to_thread(
                    blob.upload_from_file,
                    data,
                    content_type=content_type,
                )
            logger.info("✅ File uploaded from bytes: %s", object_name)
            return StorageFileUploadResponse(
                name=object_name,
                size=blob.size,
                storage_id=str(blob.generation),
                storage_link=self.generate_public_url(object_name),
            )
        except GoogleCloudError as e:
            logger.error("❌ Upload failed for %s: %s", object_name, e)
            raise FileUploadError(str(e), filename=object_name)

    async def download_file(self, object_name: str) -> tuple[bytes, str]:
        """Download a file from the bucket.

        Returns:
            Tuple of (file_content_bytes, content_type).
        """
        blob = self.bucket.blob(object_name)
        try:
            content = await asyncio.to_thread(blob.download_as_bytes)
            await asyncio.to_thread(blob.reload)
            content_type = blob.content_type or "application/octet-stream"
            return content, content_type
        except NotFound:
            raise EntityNotFoundError("File", object_name)
        except GoogleCloudError as e:
            raise StorageAPIError(str(e), operation="download")

    async def delete_file(self, object_name: str) -> bool:
        """Delete a file from the bucket."""
        blob = self.bucket.blob(object_name)
        try:
            await asyncio.to_thread(blob.delete)
            logger.info("🗑️ File deleted: %s", object_name)
            return True
        except NotFound:
            raise EntityNotFoundError("File", object_name)
        except GoogleCloudError as e:
            raise StorageAPIError(str(e), operation="delete")

    async def file_exists(self, object_name: str) -> bool:
        """Check if a file exists in the bucket."""
        blob = self.bucket.blob(object_name)
        return await asyncio.to_thread(blob.exists)

    async def get_file_metadata(self, object_name: str) -> StorageFileResponse:
        """Get metadata for a file in the bucket."""
        blob = self.bucket.blob(object_name)
        try:
            await asyncio.to_thread(blob.reload)
        except NotFound:
            raise EntityNotFoundError("File", object_name)

        return StorageFileResponse(
            name=blob.name,
            size=blob.size,
            content_type=blob.content_type,
            created_at=blob.time_created,
            updated_at=blob.updated,
            storage_class=blob.storage_class,
            md5_hash=blob.md5_hash,
        )

    async def list_files(
        self,
        prefix: str | None = None,
        max_results: int = 100,
        page_token: str | None = None,
    ) -> StorageSearchResponse:
        """List files in the bucket with optional prefix filter."""
        try:
            kwargs: dict = {
                "max_results": max_results,
                "prefix": prefix,
            }
            if page_token:
                kwargs["page_token"] = page_token

            iterator = self.client.list_blobs(self.bucket, **kwargs)
            page = await asyncio.to_thread(lambda: next(iterator.pages))

            files = []
            for blob in page:
                files.append(StorageFileResponse(
                    name=blob.name,
                    size=blob.size,
                    content_type=blob.content_type,
                    created_at=blob.time_created,
                    updated_at=blob.updated,
                    storage_class=blob.storage_class,
                    md5_hash=blob.md5_hash,
                    public_url=self.generate_public_url(blob.name),
                ))

            return StorageSearchResponse(
                files=files,
                total=len(files),
                next_page_token=iterator.next_page_token,
            )
        except GoogleCloudError as e:
            raise StorageAPIError(str(e), operation="list_files")

    # =========================================================================
    # URL Generation
    # =========================================================================

    async def generate_signed_url(
        self,
        object_name: str,
        expiration_minutes: int | None = None,
    ) -> str:
        """Generate a signed URL for temporary access to a file.

        Args:
            object_name: Object name in the bucket.
            expiration_minutes: URL validity in minutes (uses config default).

        Returns:
            Signed URL string.
        """
        minutes = expiration_minutes or settings.gcs_signed_url_expiration
        blob = self.bucket.blob(object_name)
        try:
            url = await asyncio.to_thread(
                blob.generate_signed_url,
                version="v4",
                expiration=timedelta(minutes=minutes),
                method="GET",
            )
            return url
        except Exception as e:
            logger.error("❌ Signed URL generation failed for %s: %s", object_name, e)
            raise StorageAPIError(str(e), operation="generate_signed_url")

    def generate_public_url(self, object_name: str) -> str:
        """Generate a public URL for a file.

        Requires the bucket or object to have public access configured.
        """
        return f"https://storage.googleapis.com/{settings.gcs_bucket_name}/{quote(object_name, safe='')}"

    # =========================================================================
    # Bucket Info & Stats
    # =========================================================================

    async def get_bucket_info(self) -> StorageQuotaResponse:
        """Get bucket metadata and lifecycle rules."""
        try:
            bucket = await asyncio.to_thread(
                self.client.get_bucket, settings.gcs_bucket_name
            )
            lifecycle_rules = []
            if bucket.lifecycle_rules:
                lifecycle_rules = [dict(rule) for rule in bucket.lifecycle_rules]

            return StorageQuotaResponse(
                bucket_name=bucket.name,
                location=bucket.location,
                storage_class=bucket.storage_class,
                lifecycle_rules=lifecycle_rules,
            )
        except GoogleCloudError as e:
            raise StorageAPIError(str(e), operation="get_bucket_info")

    async def get_stats(self) -> StorageStatsResponse:
        """Get file count and total size (cached, stale-while-revalidate).

        With 500k+ objects in the bucket a full scan takes several minutes.
        Stale-while-revalidate pattern:
        - Fresh cache → return immediately (O(1))
        - Expired cache with data → return stale data + background refresh
        - No data (first request) → block until the scan completes
        The Lock guarantees that only one scan runs at a time.
        """
        import time

        now = time.monotonic()
        lock = _stats_lock

        # Fresh cache — immediate response, no lock needed
        if _stats_cache["data"] and (now - _stats_cache["ts"]) < _STATS_TTL:
            return _stats_cache["data"]

        async def _do_scan() -> None:
            total_files = 0
            total_size = 0

            def _count():
                nonlocal total_files, total_size
                for blob in self.client.list_blobs(self.bucket):
                    total_files += 1
                    total_size += blob.size or 0

            await asyncio.to_thread(_count)
            _stats_cache["data"] = StorageStatsResponse(
                total_files=total_files,
                total_size_bytes=total_size,
                total_size_human=_humanize_bytes(total_size),
            )
            _stats_cache["ts"] = time.monotonic()

        if _stats_cache["data"] is not None:
            # Stale data: return immediately and refresh in background
            if not lock.locked():
                async def _bg():
                    async with lock:
                        try:
                            await _do_scan()
                        except Exception as e:
                            logger.warning("get_stats background refresh failed: %s", e)
                asyncio.create_task(_bg())
            return _stats_cache["data"]

        # No data: wait for the scan (first request or after restart)
        async with lock:
            # Another request may have completed the scan while we were waiting
            if _stats_cache["data"] and (now - _stats_cache["ts"]) < _STATS_TTL:
                return _stats_cache["data"]
            try:
                await _do_scan()
            except Exception as e:
                raise StorageAPIError(str(e), operation="get_stats")

        return _stats_cache["data"]

    # =========================================================================
    # Batch Operations
    # =========================================================================

    async def batch_upload(
        self,
        files: list[tuple[str, str, str]],
        *,
        skip_existing: bool = True,
    ) -> BatchUploadResponse:
        """Upload multiple files.

        Args:
            files: List of (local_path, object_name, content_type) tuples.
            skip_existing: Skip files that already exist in the bucket.

        Returns:
            Batch upload response with per-file results.
        """
        items: list[BatchUploadItem] = []
        successful = 0
        failed = 0

        for local_path, object_name, content_type in files:
            try:
                result = await self.upload_file(
                    local_path,
                    object_name,
                    content_type,
                    skip_existing=skip_existing,
                )
                items.append(BatchUploadItem(
                    filename=object_name,
                    success=True,
                    storage_id=result.storage_id,
                    storage_link=result.storage_link,
                ))
                successful += 1
            except Exception as e:
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
