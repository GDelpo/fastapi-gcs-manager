import re
from pathlib import Path

import pytest

from app.config import Settings, settings

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "dashboard" / "templates"


def test_upload_within_limit(as_role, storage, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)
    response = as_role("service").post(
        "/api/v1/files", files={"file": ("a.pdf", b"%PDF-1.7 x", "application/pdf")}
    )

    assert response.status_code == 200
    assert storage.uploaded == {"a.pdf": b"%PDF-1.7 x"}


def test_upload_over_limit_is_rejected(as_role, storage, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)
    big = b"x" * (1024 * 1024 + 1)
    response = as_role("service").post(
        "/api/v1/files", files={"file": ("big.bin", big, "application/octet-stream")}
    )

    assert response.status_code == 413
    assert storage.uploaded == {}


def test_batch_upload_rejects_oversized_items_only(as_role, storage, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)
    files = [
        ("files", ("ok.txt", b"small", "text/plain")),
        ("files", ("big.bin", b"x" * (1024 * 1024 + 1), "application/octet-stream")),
    ]
    response = as_role("service").post("/api/v1/files/batch", files=files)

    assert response.status_code == 200
    body = response.json()
    assert (body["successful"], body["failed"]) == (1, 1)
    assert list(storage.uploaded) == ["ok.txt"]


@pytest.mark.parametrize(("role", "status"), [("admin", 200), ("service", 403), ("agent", 403)])
def test_only_admins_delete(as_role, storage, role, status):
    response = as_role(role).delete("/api/v1/files/reports/a.pdf")

    assert response.status_code == status
    assert storage.deleted == (["reports/a.pdf"] if status == 200 else [])


def test_agents_cannot_upload(as_role, storage):
    response = as_role("agent").post("/api/v1/files", files={"file": ("a.txt", b"x", "text/plain")})
    assert response.status_code == 403


def test_skip_auth_requires_debug(monkeypatch):
    monkeypatch.setenv("SKIP_AUTH", "true")
    monkeypatch.setenv("DEBUG", "false")
    with pytest.raises(ValueError, match="SKIP_AUTH"):
        Settings()

    monkeypatch.setenv("DEBUG", "true")
    assert Settings().skip_auth is True


def test_dashboard_never_interpolates_into_inline_handlers():
    """Regression guard for the stored XSS: no ${...} inside onclick/on* attributes."""
    offenders = []
    for template in TEMPLATES.rglob("*.html"):
        for number, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r'\son\w+="[^"]*\$\{', line):
                offenders.append(f"{template.name}:{number}")
    assert offenders == []


def test_esc_helper_escapes_quotes():
    helpers = (TEMPLATES / "_js" / "_helpers.js.html").read_text(encoding="utf-8")
    assert "&quot;" in helpers and "&#39;" in helpers
