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

Generate a secret key:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Hash your admin password (or auto-generate one):
```bash
python -m app.security
```

You'll be prompted to enter a password, or just press **Enter** to have one generated for you. Either way the script prints the `ADMIN_PASSWORD_HASH=...` line ready to paste into `.env`.

Paste both values into `.env`:
```env
SECRET_KEY=<your-generated-key>
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=<output-from-above>
SCAN_SUBNETS=192.168.1.0/24
```

### 2. Run with Docker Compose

```bash
docker compose up -d
```

The API listens on `http://<host>:8000`.  
The admin UI is at `http://<host>:8000/admin`.

---

## API Authentication

All REST endpoints require an `Authorization` header:

```
Authorization: Bearer zebra_<your-api-key>
```

API keys are created through the admin UI at `/admin/keys`.

---

## REST Endpoints

### Printers

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/printers` | List all known printers |
| `GET` | `/printers/{id}/status` | Refresh and return live status for a printer |
| `POST` | `/printers/scan` | Trigger a new subnet scan |

### Templates

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/templates` | List all label templates |

### Print Jobs

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/print` | Submit a print job (returns 202 + job object) |
| `GET` | `/jobs/{id}` | Poll a job for its current status |

#### `POST /print` request body

```json
{
  "printer_id": "abc123",
  "template_id": "def456",
  "variables": {
    "sku": "WIDGET-001",
    "qty": "5"
  },
  "quantity": 2
}
```

`quantity` controls the `^PQ` count injected into the ZPL (how many copies the printer cuts).

#### Job status values

| Status | Meaning |
|--------|---------|
| `pending` | Queued, not yet sent |
| `processing` | Currently sending to printer |
| `completed` | Sent successfully |
| `failed` | Print failed; see `error_message` |

---

## Configuration

All settings are read from `.env` (or environment variables).

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | — | **Required.** Min 32 chars. Used to sign admin session JWTs — never exposed to API clients. Keep it secret; rotate it to invalidate all active admin sessions. |
| `ADMIN_USERNAME` | `admin` | Admin login username |
| `ADMIN_PASSWORD_HASH` | — | **Required.** bcrypt hash of the admin password |
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
