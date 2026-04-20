# fastapi-gcs-manager

<p>
  <img alt="Language" src="https://img.shields.io/badge/python-3.12%2B-blue?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.128-009688?logo=fastapi&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Status" src="https://img.shields.io/badge/status-stable-green">
</p>

> FastAPI microservice to manage Google Cloud Storage buckets — upload, download, list, delete and signed URLs — with a built-in admin dashboard.

## Features

- **GCS file management**: upload (single/batch), download, list, delete, metadata lookup.
- **Signed URLs**: temporary download links with configurable TTL.
- **Bucket stats & quota**: usage, object count, lifecycle rules.
- **Admin dashboard**: self-hosted Tailwind + Jinja2 UI with login via external identity service (or `SKIP_AUTH=true`).
- **JWT auth (optional)**: pluggable — validates tokens against any identity service exposing `GET /me`. Disable with `SKIP_AUTH=true` for local work.
- **Infrastructure-agnostic**: runs locally, behind Traefik, Nginx, or any reverse proxy. Respects `X-Forwarded-Prefix`.
- **Dockerized**: multi-stage build, non-root user, healthcheck. Compose variants for dev / standalone / Traefik.

## Quickstart

### Requirements

- Python 3.12+ (3.14 recommended)
- A Google Cloud Storage bucket + service account JSON with `Storage Object Admin` role
- (Optional) An identity service exposing `POST /login` and `GET /me` for token validation

### Install

```bash
git clone https://github.com/GDelpo/fastapi-gcs-manager.git
cd fastapi-gcs-manager
python -m venv env
source env/bin/activate          # Linux/Mac
# .\env\Scripts\Activate.ps1     # Windows
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
mkdir -p credentials
cp /path/to/your-service-account.json credentials/google_credentials.json
# Edit .env: GCS_BUCKET_NAME, GCS_PROJECT_ID, SKIP_AUTH, etc.
```

### Run

```bash
# Local
uvicorn app.main:app --reload --port 8000

# Docker (dev)
docker compose --env-file .env -f docker/docker-compose.dev.yml up --build

# Docker (prod, standalone)
docker compose --env-file .env -f docker/docker-compose.prod.standalone.yml up -d --build

# Docker (prod, behind Traefik on network 'traefik-public')
docker compose --env-file .env -f docker/docker-compose.prod.traefik.yml up -d --build
```

Open:
- API docs: `http://localhost:8000/docs`
- Dashboard: `http://localhost:8000/dashboard/`
- Health: `http://localhost:8000/health`

## Configuration

All variables live in `.env` (see `.env.example`):

| Variable | Description | Default |
|----------|-------------|---------|
| `GCS_BUCKET_NAME` | Target bucket name | `your-bucket-name` |
| `GCS_CREDENTIALS_PATH` | Path to service account JSON (inside container) | `/app/credentials/google_credentials.json` |
| `GCS_PROJECT_ID` | GCP project ID (optional; inferred from JSON) | — |
| `GCS_SIGNED_URL_EXPIRATION` | Signed URL TTL in minutes | `60` |
| `GCS_LIFECYCLE_STANDARD_DAYS` | Days before auto Nearline transition (info) | `90` |
| `IDENTITY_SERVICE_URL` | Server-to-server URL for token validation | `http://identity_api:8080/api/v1` |
| `IDENTITY_EXTERNAL_URL` | Browser-facing URL used by the dashboard login page | `http://localhost:8080/api/v1` |
| `SKIP_AUTH` | Disable auth (dev only) | `false` |
| `SOURCE_MOUNT_PATH` | Optional in-container path for bulk uploads from a mounted folder | `/app/source` |
| `MAX_UPLOAD_SIZE_MB` | Max upload size per request | `100` |
| `CORS_ORIGINS` | JSON list of allowed origins | `["*"]` |
| `DEBUG` | Enables reload + verbose logs | `false` |
| `LOG_FORMAT` | `json` or `text` | `json` |

## Endpoints (main)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/files` | List files (cursor-based pagination) |
| GET | `/api/v1/files/{name}` | File metadata |
| GET | `/api/v1/files/{name}/link` | Signed download URL |
| GET | `/api/v1/files/{name}/download` | Stream file contents |
| POST | `/api/v1/files` | Upload file (multipart) |
| POST | `/api/v1/files/batch` | Batch upload |
| DELETE | `/api/v1/files/{name}` | Delete file |
| GET | `/api/v1/quota` | Bucket stats |
| GET | `/health` | Health check (cached 30s) |

## Architecture

```
app/
├── config.py          # pydantic-settings
├── service.py         # Core GCS logic (upload, download, signed URLs)
├── api.py             # FastAPI routers
├── main.py            # App factory, lifespan, health
├── schemas.py         # Pydantic request/response (camelCase)
├── auth.py            # Pluggable token validation
├── dependencies.py    # FastAPI DI
├── exceptions.py      # Custom exceptions + handlers
├── logger.py          # JSON / text logging
├── middleware.py      # Request logging + ProxyHeaders
└── dashboard/         # Admin UI (Jinja2 + Tailwind + Lucide)
```

### Notes

- **Pagination**: GCS uses cursor-based paging (page token). No `total` count is exposed — this is a limitation of the underlying API.
- **Signed URLs**: short-lived by design; do not cache on the client side beyond `GCS_SIGNED_URL_EXPIRATION`.
- **Auth**: any identity service exposing `POST /login` + `GET /me` works. Alternatively, run with `SKIP_AUTH=true` for local/single-tenant setups.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
pytest --cov=app --cov-report=html
```

## License

[MIT](LICENSE) © 2026 Guido Delponte
