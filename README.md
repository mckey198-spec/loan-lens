# LoanLens

Retail loan verification, risk assessment, and underwriting support platform.

LoanLens helps lending teams ingest applicant documents, run deterministic verification rules, orchestrate processing workflows, and track underwriting decisions with clear data lineage from extraction through human correction.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12+, FastAPI, Pydantic v2 |
| Data access | PostgreSQL 16 (`pgvector`), raw SQL via psycopg3 |
| Migrations | Alembic (handwritten SQL) |
| Workflow | LangGraph-style state machine via background workers |
| Frontend | Vite + React (TypeScript) SPA in `apps/web/` |
| Runtime | Docker Compose (`api`, `worker`, `db`, `scanner`, `web`) |
| Virus scan | ClamAV (with mock fallback for local/tests) |

Financial arithmetic and verification gates (BR-01–BR-11) are pure, deterministic Python — never delegated to an LLM.

---

## Getting Started

### Prerequisites

- Docker Desktop (or Docker Engine + Compose v2)
- Python 3.12+ (for local API/tests outside Compose)
- Node.js 20+ (for the web SPA)

### 1. Clone

```bash
git clone https://github.com/mckey198-spec/loan-lens.git
cd loan-lens
```

### 2. Environment

```bash
cp .env.example .env
# Edit .env with local values as needed. Do not commit secrets.
```

### 3. Run with Docker Compose (recommended)

```bash
docker compose up --build
```

Typical local endpoints:

| Service | URL |
|---|---|
| API | http://localhost:8000 |
| Web SPA | http://localhost:5173 (or Compose-mapped port) |
| Postgres | localhost:5432 |

### 4. Local API (without full Compose)

```bash
python -m venv .venv

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
alembic upgrade head
uvicorn apps.api.main:app --reload --port 8000
```

### 5. Frontend

```bash
cd apps/web
npm install
npm run dev
```

### 6. Tests

```bash
# Backend
pytest

# Frontend
cd apps/web
npm test
```

---

## Environment Variables

Copy `.env.example` to `.env`. Key variables:

| Variable | Purpose | Example |
|---|---|---|
| `APP_ENV` | Deployment environment | `local` |
| `DEBUG` | Verbose / development behaviour | `false` |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://postgres:postgres@localhost:5432/loanlens` |
| `QUARANTINE_DIR` | Pre-scan upload storage | `./data/quarantine` |
| `CLEAN_UPLOADS_DIR` | Post-scan clean storage | `./data/clean_uploads` |
| `CLAMAV_HOST` / `CLAMAV_PORT` | ClamAV scanner endpoint | `localhost` / `3310` |
| `CLAMAV_BACKEND` | `mock` or `clamav` | `mock` |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Optional LLM providers | _(empty locally)_ |
| `CORS_ALLOWED_ORIGINS` | Allowed SPA origins | `http://localhost:5173` |
| `SESSION_*` / `ARGON2_*` | Session cookie & password hashing | see `.env.example` |

Full template: [`.env.example`](.env.example).

---

## Repository Layout

```
apps/          # API and web SPA
modules/       # Domain modules / services
shared/        # Shared types, errors, utilities
providers/     # External provider adapters
alembic/       # Handwritten SQL migrations
docs/          # Architecture, ADRs, tooling rules
tests/         # Backend tests
scripts/       # Operational helpers
```

Governance docs:

- [`docs/AGENT_RULES.md`](docs/AGENT_RULES.md)
- [`docs/TOOLS.md`](docs/TOOLS.md)
- [`docs/architecture/`](docs/architecture/)

---

## Contributing

### Branching

| Branch | Purpose |
|---|---|
| `main` | Stable integration branch |
| `feature/<ticket-or-slug>` | New functionality |
| `fix/<ticket-or-slug>` | Bug fixes |
| `chore/<slug>` | Tooling, docs, deps |
| `docs/<slug>` | Documentation only |

Keep branches short-lived and focused on one change set.

### Pull requests

1. Branch from the latest `main`.
2. Keep commits focused; prefer clear “why” messages.
3. Ensure tests pass locally (`pytest`, `npm test` in `apps/web`).
4. Open a PR against `main` with:
   - Summary of intent
   - Test plan / verification steps
   - Notes on migrations, env vars, or breaking changes
5. Do not commit `.env`, secrets, build artifacts, or `node_modules`.
6. Database migrations must be handwritten Alembic SQL (no autogenerate).
7. Preserve 4-layer field lineage: `extracted` → `normalized` → `verified` → `human_corrected`.

### Code expectations

- Thin API routers; business logic in module services
- Explicit types across function boundaries
- Structured errors with `code`, `message`, `details`, and `request_id`
- Raw SQL via psycopg3 — no SQLAlchemy ORM usage in application modules

---

## License

Proprietary / all rights reserved unless otherwise stated by the repository owner.
