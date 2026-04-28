# Zebra API

A self-hosted REST API for sending ZPL print jobs to Zebra label printers over the network. Includes an admin web UI for managing printers, templates, API keys, and monitoring print jobs.

---

## Features

- **Print jobs** — send ZPL label templates to any registered Zebra printer; jobs are queued asynchronously and return a job ID immediately (HTTP 202)
- **ZPL templates** — store and manage named ZPL templates with variable substitution (`{{variable}}`) and automatic `^PQ` quantity injection
- **Printer discovery** — subnet scan on startup (and on demand) finds Zebra printers automatically; per-printer connection locking prevents send collisions
- **API key auth** — all REST endpoints require a bearer API key; keys are hashed in the database and prefixed with `zebra_`
- **Admin UI** — browser-based dashboard (cookie + JWT session) for:
  - Viewing printer status
  - Creating / editing ZPL templates with a live preview
  - Issuing and revoking API keys
  - Monitoring job history
  - Adjusting runtime settings
- **Job retention** — background task automatically purges jobs older than `JOB_RETENTION_DAYS`
- **Dockerised** — single-container deployment with `network_mode: host` so the container can reach printers on the local network

---

## Quick Start

### 1. Copy and edit the env file

```bash
cp .env.example .env
```

Edit `.env` with your subnets and any non-sensitive settings.

### 2. Set secrets in the Docker Compose override

Docker Compose treats `$` in `env_file:` values as variable substitution markers, which corrupts bcrypt hashes (they contain multiple `$` characters). To avoid this, secrets go in a gitignored override file instead.

Generate a secret key:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Hash your admin password (or auto-generate one):
```bash
python -m app.security
```

Then create `docker-compose.override.yml` (it is gitignored — never committed):
```yaml
services:
  zebra-api:
    environment:
      SECRET_KEY: "your-secret-key-here"
      ADMIN_PASSWORD_HASH: "$$2b$$12$$rest-of-your-hash"
```

> **Important:** replace every `$` in the hash with `$$` in this file. Docker Compose unescapes `$$` → `$` before passing the value to the container, so the hash arrives intact.

### 3. Run with Docker Compose

```bash
docker compose up -d
```

Compose automatically merges `docker-compose.override.yml` — no extra flags needed.

The API listens on `http://<host>:8000`.  
The admin UI is at `http://<host>:8000/admin`.

---

## API Authentication

All REST endpoints require an `Authorization` header carrying the full API key as issued by the admin UI (keys are prefixed with `zebra_`):

```
Authorization: Bearer zebra_<key-suffix>
```

The complete key string — including the `zebra_` prefix — is what you pass as the Bearer token. Do **not** add an extra `zebra_` in your client code; the key shown in the admin UI is the entire value to use.

For example, if the admin UI shows `zebra_AbCdEfGhIj...`, the header is:

```
Authorization: Bearer zebra_AbCdEfGhIj...
```

API keys are created through the admin UI at `/admin/keys`.

---

## REST Endpoints

All endpoints require the header:
```
Authorization: Bearer zebra_<your-api-key>
```

---

### `GET /printers`

Returns all known printers.

**Response `200`**
```json
[
  {
    "id": "a1b2c3d4-...",
    "ip": "192.168.1.101",
    "alias": "warehouse-1",
    "friendly_name": "ZT411",
    "product_name": "ZT41143",
    "firmware": "V75.20.01Z",
    "print_width": "4.0 in",
    "ports_open": [9100, 80],
    "is_online": true
  }
]
```

---

### `GET /printers/{id}/status`

Refreshes and returns the live status for a single printer (opens a socket to query the device).

**Path param:** `id` — printer UUID

**Response `200`**
```json
{
  "id": "a1b2c3d4-...",
  "ip": "192.168.1.101",
  "alias": "warehouse-1",
  "friendly_name": "ZT411",
  "product_name": "ZT41143",
  "firmware": "V75.20.01Z",
  "print_width": "4.0 in",
  "ports_open": [9100, 80],
  "is_online": true
}
```

**Response `404`** — printer ID not found.

---

### `POST /printers/scan`

Triggers a fresh subnet scan (uses `SCAN_SUBNETS` from config). Discovered printers are upserted into the database.

**No request body.**

**Response `200`**
```json
{
  "found": 4,
  "saved": 2
}
```

`found` = total devices responding, `saved` = new or updated records written.

---

### `GET /templates`

Returns all label templates.

**Response `200`**
```json
[
  {
    "id": "e5f6g7h8-...",
    "name": "Product Label",
    "description": "Standard product SKU label",
    "variables": ["sku", "description", "barcode"]
  }
]
```

`variables` is the list of `{{placeholder}}` names found in the ZPL body.

---

### `POST /print`

Submits a print job. The job is queued immediately and sent to the printer asynchronously.

**Request body**
```json
{
  "printer_id": "a1b2c3d4-...",
  "template_id": "e5f6g7h8-...",
  "variables": {
    "sku": "WIDGET-001",
    "description": "Blue Widget",
    "barcode": "123456789"
  },
  "quantity": 2
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `printer_id` | string (UUID) | Yes | ID of the target printer |
| `template_id` | string (UUID) | Yes | ID of the label template to use |
| `variables` | object | No | Key/value pairs to substitute into the template. Defaults to `{}` |
| `quantity` | integer ≥ 1 | No | Number of copies (`^PQ` injected into ZPL). Defaults to `1`, max `MAX_PRINT_QUANTITY` |

**Response `202`** — job accepted
```json
{
  "id": "j9k0l1m2-...",
  "printer_id": "a1b2c3d4-...",
  "template_id": "e5f6g7h8-...",
  "quantity": 2,
  "status": "pending",
  "error_message": null
}
```

**Response `400`** — unknown printer or template ID.

---

### `GET /jobs/{id}`

Polls the status of a previously submitted print job.

**Path param:** `id` — job UUID (returned by `POST /print`)

**Response `200`**
```json
{
  "id": "j9k0l1m2-...",
  "printer_id": "a1b2c3d4-...",
  "template_id": "e5f6g7h8-...",
  "quantity": 2,
  "status": "completed",
  "error_message": null
}
```

**Response `404`** — job ID not found.

#### Job status values

| Status | Meaning |
|--------|---------|
| `pending` | Queued, not yet sent |
| `processing` | Currently sending to printer |
| `completed` | Sent successfully |
| `failed` | Send failed — see `error_message` for detail |

---

## Configuration

All settings are read from `.env` (or environment variables).

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | — | **Required.** Min 32 chars. Used to sign admin session JWTs — never exposed to API clients. Keep it secret; rotate it to invalidate all active admin sessions. |
| `ADMIN_USERNAME` | `admin` | Admin login username |
| `ADMIN_PASSWORD_HASH` | — | **Required.** bcrypt hash of the admin password. Set in `docker-compose.override.yml` with `$` escaped as `$$` |
| `ADMIN_JWT_MINUTES` | `480` | Admin session cookie lifetime (minutes) |
| `COOKIE_SECURE` | `false` | Set `true` in production behind HTTPS |
| `SCAN_SUBNETS` | `` | Comma-separated CIDR ranges to scan, e.g. `192.168.1.0/24` |
| `SCAN_ON_STARTUP` | `true` | Run a printer scan when the container starts |
| `SCAN_PORTS` | `9100,515,631,80` | Ports checked during discovery |
| `SCAN_CONNECT_TIMEOUT_SECONDS` | `0.5` | Per-host timeout during scan |
| `SCANNER_WORKERS` | `100` | Concurrent scan workers |
| `PRINTER_PORT` | `9100` | Port used to send ZPL |
| `PRINTER_CONNECT_TIMEOUT_SECONDS` | `3.0` | TCP connect timeout |
| `PRINTER_WRITE_TIMEOUT_SECONDS` | `5.0` | Socket write timeout |
| `PRINTER_READ_TIMEOUT_SECONDS` | `3.0` | Socket read timeout |
| `PRINTER_STATUS_TIMEOUT_SECONDS` | `2.0` | Status query timeout |
| `MAX_PRINT_QUANTITY` | `100` | Maximum `^PQ` quantity accepted |
| `MAX_ZPL_BYTES` | `262144` | Maximum ZPL payload size (256 KB) |
| `JOB_RETENTION_DAYS` | `31` | Days before completed jobs are purged |
| `CLEANUP_INTERVAL_MINUTES` | `60` | How often the cleanup task runs |

---

## Development

### Requirements

- Python 3.12+
- A `.env` file (see above)

### Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run locally

```bash
uvicorn app.main:app --reload
```

### Run tests

```bash
pytest
```

---

## Project Layout

```
app/
├── auth/           API key dependency (Bearer token extraction + DB lookup)
├── models/
│   ├── db.py       SQLAlchemy ORM models (Printer, LabelTemplate, PrintJob, ApiKey, AdminUser)
│   └── schemas.py  Pydantic request/response schemas
├── routers/
│   ├── admin.py    Admin UI routes (login, dashboard, printers, templates, keys, jobs, settings)
│   ├── jobs.py     POST /print, GET /jobs/{id}
│   ├── printers.py GET /printers, POST /printers/scan
│   └── templates.py GET /templates
├── services/
│   ├── jobs.py        Job creation, async processing, printer upsert, status refresh, cleanup
│   ├── printer_io.py  Raw TCP socket send with per-printer asyncio lock
│   ├── scanner.py     Async subnet scanner (concurrent TCP probing)
│   └── zpl_render.py  Variable substitution + ^PQ injection into ZPL
├── templates/      Jinja2 HTML templates for the admin UI
├── config.py       Pydantic Settings (loaded from .env)
├── database.py     SQLAlchemy engine + session factory + init_db()
├── main.py         FastAPI app factory + lifespan (startup/shutdown)
└── security.py     bcrypt hashing, JWT creation/verification, API key generation
tests/              pytest test suite (59 tests, no external dependencies)
```

---

## Security Notes

- API keys are stored as bcrypt hashes — the raw key is only shown once at creation time.
- Admin sessions use signed JWT cookies (`HS256`); set `COOKIE_SECURE=true` and serve over HTTPS in production.
- The container uses `network_mode: host` to reach printers on the LAN. Do not expose port 8000 publicly without a reverse proxy and appropriate firewall rules.
- ZPL payload size is capped at `MAX_ZPL_BYTES` (default 256 KB) to prevent abuse.
