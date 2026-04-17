# Storage Service

Microservicio FastAPI para gestión de archivos en Google Cloud Storage. Usado por el dispatcher para sincronizar PDFs de pólizas al bucket `noble-polizas` y generar signed URLs para descarga.

## Repositorio

```bash
git clone http://192.168.190.95/forgejo/noble/storage.git
git pull origin main   # actualizar
```

> Primera vez en una máquina nueva: ver [SETUP.md](http://192.168.190.95/forgejo/noble/workspace/raw/branch/main/SETUP.md) para configurar proxy y credenciales Git.

## Dependencias

- **Upstream**: Google Cloud Storage (bucket `noble-polizas`), filesystem local (`/media/polizas` — share NAS)
- **Downstream**: `dispatcher` (usa `bulk_sync` y signed URLs), cualquier cliente autenticado

## Tech Stack

| Componente | Versión | Notas |
|-----------|---------|-------|
| Python | 3.14 | |
| FastAPI | 0.128.0 | |
| Pydantic | 2.12.5 | |
| google-cloud-storage | latest | Service Account JSON en `credentials/` |
| Redis | — | Solo para health cache (30s) |

## Endpoints

### Archivos

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/api/v1/files` | Listar archivos (filtros y paginación cursor-based) |
| GET | `/api/v1/files/{object_name}` | Metadata de archivo |
| GET | `/api/v1/files/{object_name}/link` | Signed URL de descarga |
| GET | `/api/v1/files/{object_name}/download` | Descarga directa del contenido |
| POST | `/api/v1/files` | Subir archivo (multipart) |
| POST | `/api/v1/files/batch` | Subir múltiples archivos |
| DELETE | `/api/v1/files/{object_name}` | Eliminar archivo |

### Sync (usado por el dispatcher)

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| POST | `/api/v1/sync/bulk` | Sincronizar PDFs desde path local al bucket |
| GET | `/api/v1/sync/status/{job_id}` | Estado de un job de sync |

### Utilidades

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/api/v1/quota` | Estadísticas del bucket |
| GET | `/api/v1/stats` | Estadísticas (archivos, tamaño) |
| GET | `/health` | Health check (caché 30s) |

---

## Configuración

```bash
cp .env.example .env
# Colocar service account JSON en credentials/google_credentials.json
```

| Variable | Descripción | Ejemplo |
|----------|-------------|---------|
| `GCS_BUCKET_NAME` | Nombre del bucket GCS | `noble-polizas` |
| `GOOGLE_CREDENTIALS_PATH` | Path al JSON de service account | `/app/credentials/google_credentials.json` |
| `IDENTITY_SERVICE_URL` | URL identidad (server-to-server) | `http://identidad_api:8080/api/v1` |
| `IDENTITY_EXTERNAL_URL` | URL identidad (browser/Swagger) | `http://192.168.190.95/identidad/api/v1` |
| `SKIP_AUTH` | Deshabilitar auth en dev | `false` (prod) / `true` (dev) |

> El archivo `credentials/google_credentials.json` **nunca se commitea** (está en `.gitignore`). En producción se monta como volumen read-only.

---

## Desarrollo local

```bash
cd envio_polizas/storage
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

pip install -r requirements.txt -r requirements-dev.txt

cp .env.example .env
# Colocar credentials/google_credentials.json con acceso al bucket

uvicorn app.main:app --reload --port 8000
```

---

## Docker (Producción con Traefik)

```bash
docker compose --env-file .env -f docker/docker-compose.prod.traefik.yml up -d --build
```

Volúmenes críticos en producción:

| Path en host | Path en container | Descripción |
|---|---|---|
| `../credentials` | `/app/credentials` | Service account Google (ro) |
| `/media/polizas` | `/media/polizas` | Share NAS de PDFs (ro, usado por bulk sync) |

---

## Tests

```bash
pytest
pytest --cov=app --cov-report=html
```

---

## Arquitectura de archivos

```
app/
├── config.py          # Settings (pydantic-settings)
├── service.py         # Core GCS logic (upload, download, sync, signed URLs)
├── api.py             # Endpoints FastAPI
├── main.py            # App factory, lifespan, health
├── schemas.py         # Request/Response (CamelCase)
├── auth.py            # Auth via identidad
├── dependencies.py    # FastAPI DI
├── exceptions.py      # Custom exceptions + handlers
├── logger.py          # JSON/Text logging
├── middleware.py      # Request logging + proxy headers
└── dashboard/         # Dashboard web integrado
```

### Paginación

GCS usa paginación cursor-based (page token) — no hay `total` de resultados disponible. El dashboard lo documenta como limitación conocida.

---

## Troubleshooting

### Error de autenticación con GCS

**Síntoma**: `google.auth.exceptions.DefaultCredentialsError`  
**Causa**: El JSON de service account no está en el path esperado o tiene permisos incorrectos  
**Fix**:
```bash
# Verificar que el archivo existe dentro del container
docker exec -it storage_api ls -la /app/credentials/

# Verificar permisos del volumen en el host (appuser uid=1000 necesita leer)
chmod o+r /opt/microservicios/credentials/google_credentials.json
```

### Signed URL inválida o expirada

**Síntoma**: El link descargado retorna 403 o "SignatureExpired"  
**Causa**: Las signed URLs tienen TTL corto (default 15 min)  
**Fix**: El dispatcher genera URLs frescas al momento del envío — no cachear las URLs en el cliente.

### Bulk sync falla silenciosamente

**Síntoma**: El dispatcher reporta sync completado pero los archivos no aparecen en GCS  
**Fix**:
```bash
# Ver logs del container durante el sync
docker logs -f storage_api | grep -E "sync|error|ERROR"

# Verificar que /media/polizas está montado y tiene contenido
docker exec -it storage_api ls /media/polizas/ | head -5
```

### Container no accede a GCS (desde red interna)

**Síntoma**: Timeout al conectar con `storage.googleapis.com`  
**Causa**: El servidor no tiene salida a internet en ese puerto  
**Fix**: Verificar que el firewall permite salida a `storage.googleapis.com:443`

```bash
docker exec -it storage_api curl -I https://storage.googleapis.com
```
