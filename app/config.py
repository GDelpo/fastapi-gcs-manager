"""Application settings and configuration."""

from pydantic import Field, SecretStr, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    This configuration is infrastructure-agnostic. The application runs
    the same way locally or behind any reverse proxy (Traefik, Nginx, etc.).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # =========================================================================
    # Environment
    # =========================================================================
    debug: bool = False
    environment: str | None = None

    # =========================================================================
    # Logging
    # =========================================================================
    log_level: str | None = None
    log_format: str = "json"
    log_file: str | None = None

    # =========================================================================
    # Application Metadata
    # =========================================================================
    project_name: str = "FastAPI GCS Manager"
    project_version: str = "1.0.0"
    project_description: str = "Google Cloud Storage file management microservice"
    api_prefix: str = "/api/v1"

    # =========================================================================
    # CORS
    # =========================================================================
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    # =========================================================================
    # Google Cloud Storage
    # =========================================================================
    gcs_bucket_name: str = ""
    gcs_credentials_path: str = "/app/credentials/google_credentials.json"
    gcs_project_id: str = ""
    gcs_signed_url_expiration: int = 60  # minutes
    gcs_lifecycle_standard_days: int = 90  # days before Nearline transition

    # =========================================================================
    # Identity Service (for token validation)
    # =========================================================================
    identity_service_url: str = "http://localhost:8080/api/v1"
    identity_external_url: str = ""
    skip_auth: bool = False

    @computed_field
    @property
    def identity_login_url(self) -> str:
        """URL for Swagger UI login (must be browser-accessible)."""
        base = self.identity_external_url or self.identity_service_url
        return f"{base}/login"

    @computed_field
    @property
    def identity_me_url(self) -> str:
        """URL to validate tokens and get user info."""
        return f"{self.identity_service_url}/me"

    # =========================================================================
    # Optional source mount (for bulk upload jobs from a local/mounted folder)
    # =========================================================================
    source_mount_path: str = "/app/source"

    # =========================================================================
    # Upload Settings
    # =========================================================================
    max_upload_size_mb: int = 100
    allowed_mime_types: list[str] = Field(
        default_factory=lambda: [
            "application/pdf",
            "image/jpeg",
            "image/png",
            "text/plain",
            "text/csv",
        ]
    )

    # =========================================================================
    # Validation
    # =========================================================================
    @model_validator(mode="after")
    def validate_settings(self):
        """Derive defaults for optional fields."""
        if self.environment is None:
            object.__setattr__(
                self, "environment", "development" if self.debug else "production"
            )
        if self.log_level is None:
            object.__setattr__(
                self, "log_level", "DEBUG" if self.debug else "INFO"
            )
        else:
            object.__setattr__(self, "log_level", self.log_level.upper())
        return self


# Global settings instance
settings = Settings()
