"""Custom exceptions and handlers."""

from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from app.logger import get_logger
from app.schemas import ErrorResponse

logger = get_logger(__name__)


# ============================================================================
# Custom Exceptions
# ============================================================================


class ServiceException(Exception):
    """Base service exception."""

    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        details: dict[str, Any] | None = None,
    ):
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)


class EntityNotFoundError(ServiceException):
    """Entity not found."""

    def __init__(self, entity: str, identifier: str | int):
        super().__init__(
            message=f"{entity} '{identifier}' not found",
            status_code=status.HTTP_404_NOT_FOUND,
            details={"entity": entity, "identifier": str(identifier)},
        )


class AuthenticationError(ServiceException):
    """Authentication failed."""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
            details={"www_authenticate": "Bearer"},
        )


class AuthorizationError(ServiceException):
    """Authorization failed."""

    def __init__(self, message: str = "Insufficient permissions"):
        super().__init__(message=message, status_code=status.HTTP_403_FORBIDDEN)


class StorageAPIError(ServiceException):
    """Google Cloud Storage API error."""

    def __init__(self, message: str, operation: str | None = None):
        super().__init__(
            message=f"Storage API error: {message}",
            status_code=status.HTTP_502_BAD_GATEWAY,
            details={"operation": operation} if operation else {},
        )


class FileUploadError(ServiceException):
    """File upload failed."""

    def __init__(self, message: str, filename: str | None = None):
        super().__init__(
            message=f"Upload failed: {message}",
            status_code=status.HTTP_400_BAD_REQUEST,
            details={"filename": filename} if filename else {},
        )


class QuotaExceededError(ServiceException):
    """Storage quota exceeded."""

    def __init__(self, message: str = "Storage quota exceeded"):
        super().__init__(
            message=message,
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
        )


# ============================================================================
# Exception Handlers
# ============================================================================


async def service_exception_handler(
    request: Request,
    exc: ServiceException,
) -> JSONResponse:
    """Handle ServiceException and subclasses."""
    logger.warning(
        f"Service exception: {exc.message}",
        extra={"extra_fields": {"status_code": exc.status_code, **exc.details}},
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=type(exc).__name__,
            message=exc.message,
            details=exc.details,
        ).model_dump(),
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError | PydanticValidationError,
) -> JSONResponse:
    """Handle validation errors."""
    if isinstance(exc, RequestValidationError):
        errors = exc.errors()
    else:
        errors = exc.errors()

    logger.warning(
        "Validation error",
        extra={"extra_fields": {"errors": errors}},
    )

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(
            error="ValidationError",
            message="Request validation failed",
            details={"errors": errors},
        ).model_dump(),
    )


async def general_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Handle unexpected exceptions."""
    logger.error(
        "Unhandled exception: %s",
        str(exc),
        exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            error="InternalError",
            message="An unexpected error occurred",
        ).model_dump(),
    )
