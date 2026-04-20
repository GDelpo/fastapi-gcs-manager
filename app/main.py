"""Storage Manager - Main application entry point.

Infrastructure-agnostic: runs the same way locally, behind Traefik,
or behind any other reverse proxy.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import asyncio
import time

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError as PydanticValidationError

from app.api import router as storage_router
from app.config import settings
from app.dashboard.routes import router as dashboard_router
from app.exceptions import (
    ServiceException,
    general_exception_handler,
    service_exception_handler,
    validation_exception_handler,
)
from app.logger import configure_logging, get_logger
from app.middleware import RequestLoggingMiddleware, ProxyHeadersMiddleware
from app.schemas import DependencyStatus, HealthResponse
from app.service import StorageService

# Configure logging before anything else
configure_logging()
logger = get_logger(__name__)


# Health cache (avoid hitting GCS on every /health call)
_health_cache: dict = {"data": None, "ts": 0}
_HEALTH_TTL = 30  # seconds


async def _probe_dependencies(storage_service: StorageService) -> dict[str, DependencyStatus]:
    """Probe all dependencies and return their status."""
    deps: dict[str, DependencyStatus] = {}

    # Google Cloud Storage
    start = time.monotonic()
    try:
        connected = await asyncio.wait_for(storage_service.test_connection(), timeout=5.0)
        latency = (time.monotonic() - start) * 1000
        deps["google_cloud_storage"] = DependencyStatus(
            status="healthy" if connected else "unhealthy",
            latency_ms=round(latency, 1),
        )
    except Exception as e:
        deps["google_cloud_storage"] = DependencyStatus(
            status="unhealthy",
            error=str(e),
        )

    return deps


@asynccontextmanager
async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
    """Manage application lifespan: startup and shutdown events."""
    logger.info(
        "🚀 Starting Storage Manager",
        extra={
            "extra_fields": {
                "version": settings.project_version,
                "environment": settings.environment,
                "debug": settings.debug,
                "bucket": settings.gcs_bucket_name,
            }
        },
    )

    # Initialize Storage service
    storage_service = StorageService()
    app_instance.state.storage_service = storage_service
    try:
        connected = await asyncio.wait_for(storage_service.test_connection(), timeout=5.0)
        if connected:
            logger.info("✅ GCS bucket connection successful (bucket=%s)", settings.gcs_bucket_name)
            app_instance.state.storage_connected = True
        else:
            logger.warning("⚠️ GCS bucket connection failed — will retry in background")
            app_instance.state.storage_connected = False
    except (Exception, asyncio.TimeoutError) as e:
        logger.warning("⚠️ Could not initialize Storage service: %s", e)
        app_instance.state.storage_connected = False

    # Create shared HTTP client for auth
    app_instance.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )

    # Pre-warm caches in background
    async def _prewarm():
        try:
            logger.info("🔄 Pre-warming caches...")
            health_result, _ = await asyncio.gather(
                _probe_dependencies(storage_service),
                storage_service.get_stats(),
                return_exceptions=True,
            )
            if isinstance(health_result, dict):
                _health_cache["data"] = health_result
                _health_cache["ts"] = time.monotonic()
            logger.info("✅ Caches pre-warmed")
        except Exception as e:
            logger.warning("⚠️ Cache pre-warm failed: %s", e)

    if getattr(app_instance.state, "storage_connected", False):
        asyncio.create_task(_prewarm())

    yield

    # Close shared HTTP client
    await app_instance.state.http_client.aclose()
    logger.info("👋 Shutting down Storage Manager")


# Create FastAPI application
app = FastAPI(
    title=settings.project_name,
    description=settings.project_description,
    version=settings.project_version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
)

# =============================================================================
# Middleware
# =============================================================================

app.add_middleware(ProxyHeadersMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# =============================================================================
# Exception Handlers
# =============================================================================

app.add_exception_handler(ServiceException, service_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(PydanticValidationError, validation_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# =============================================================================
# Routers
# =============================================================================

app.include_router(storage_router)
app.include_router(dashboard_router, prefix="/dashboard")


# =============================================================================
# Health Check
# =============================================================================


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check(request: Request):
    """Health check endpoint."""
    now = time.monotonic()

    # Use cached health if fresh enough
    if _health_cache["data"] and (now - _health_cache["ts"]) < _HEALTH_TTL:
        deps = _health_cache["data"]
    else:
        storage_service = request.app.state.storage_service
        deps = await _probe_dependencies(storage_service)
        _health_cache["data"] = deps
        _health_cache["ts"] = now

    # Determine overall status
    all_healthy = all(d.status == "healthy" for d in deps.values())
    any_healthy = any(d.status == "healthy" for d in deps.values())

    if all_healthy:
        overall = "healthy"
    elif any_healthy:
        overall = "degraded"
    else:
        overall = "unhealthy"

    return HealthResponse(
        status=overall,
        version=settings.project_version,
        environment=settings.environment,
        bucket=settings.gcs_bucket_name,
        dependencies=deps,
    )


# =============================================================================
# Entrypoint
# =============================================================================

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
