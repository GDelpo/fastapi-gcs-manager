import os

import pytest
from fastapi.testclient import TestClient

# Fake settings; the tests never reach GCS or the identity service.
os.environ.setdefault("GCS_BUCKET_NAME", "test-bucket")
os.environ["SKIP_AUTH"] = "false"
os.environ["LOG_FORMAT"] = "text"

from app.auth import TokenData, get_current_user  # noqa: E402
from app.dependencies import get_storage_service  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas import StorageFileUploadResponse  # noqa: E402


class FakeStorage:
    """Stands in for StorageService (GCS)."""

    def __init__(self):
        self.uploaded: dict[str, bytes] = {}
        self.deleted: list[str] = []

    async def upload_file_from_bytes(
        self, data, object_name, content_type="", *, skip_existing=False
    ):
        self.uploaded[object_name] = data
        return StorageFileUploadResponse(
            name=object_name,
            size=len(data),
            storage_id="1",
            storage_link=f"https://storage.example/{object_name}",
        )

    async def delete_file(self, object_name):
        self.deleted.append(object_name)
        return True


@pytest.fixture
def storage():
    return FakeStorage()


@pytest.fixture
def as_role(storage):
    """Return a TestClient whose caller has the given role. No lifespan, so no GCS."""

    def make(role: str) -> TestClient:
        app.dependency_overrides[get_storage_service] = lambda: storage
        app.dependency_overrides[get_current_user] = lambda: TokenData(
            id="u1", user_name=f"{role}-user", role=role
        )
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()
