# Distributed Job Scheduler

A production-inspired distributed job scheduling platform: REST API, worker
service with atomic job claiming, retry/backoff, dead-letter handling, and a
live dashboard.

```
distributed-job-scheduler/
├── backend/
│   ├── app/                # FastAPI application (API server)
│   │   ├── main.py         # App entrypoint, wires routers + scheduler loop
│   │   ├── models.py       # SQLAlchemy 2.0 ORM models (full schema)
│   │   ├── schemas.py      # Pydantic request/response models
│   │   ├── database.py     # Async engine/session setup
│   │   ├── security.py     # Password hashing + JWT
│   │   ├── deps.py         # FastAPI dependencies (current user, etc.)
│   │   ├── routers/        # auth, projects, queues, jobs, workers, dashboard
│   │   └── services/       # retry backoff math, atomic job claim, scheduler loop
│   ├── worker/
│   │   ├── worker_main.py  # Standalone worker process
│   │   └── handlers.py     # Job handler registry (add your real handlers here)
│   ├── tests/               # pytest + httpx async tests (SQLite in-memory)
│   ├── requirements.txt
│   ├── .env.example
│   ├── Dockerfile           # API image
│   └── Dockerfile.worker    # Worker image
├── frontend/                 # Static dashboard (vanilla JS, no build step)
│   ├── index.html
│   ├── style.css
│   └── app.js
├── docs/
│   ├── ARCHITECTURE.md
│   ├── API.md
│   └── DESIGN_DECISIONS.md
└── docker-compose.yml         # Postgres + API + 2 workers + static frontend
```

## Quick start (Docker — recommended)

Requires Docker Desktop.

```bash
docker compose up --build
```

This starts:
- **postgres** on `5432`
- **api** (FastAPI) on `http://localhost:8000` — Swagger docs at `/docs`
- **worker-1** and **worker-2** — two independent worker processes claiming
  from the same queues, proving jobs are never double-executed
- **frontend** (static dashboard) on `http://localhost:3000`

Open `http://localhost:3000`, register an account, and you're in.

## Quick start (no Docker — SQLite, single process)

Useful for quickly trying things out locally without installing Postgres.

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
copy .env.example .env         # Windows: copy, macOS/Linux: cp .env.example .env

# Edit .env: comment out the Postgres DATABASE_URL, uncomment the SQLite one.

uvicorn app.main:app --reload
```

In a second terminal, from `backend/`, with the same venv active:

```bash
python -m worker.worker_main
```

Then open `frontend/index.html` directly in a browser, or serve it:

```bash
cd frontend
python -m http.server 3000
```

Point the dashboard's "API base URL" field at `http://localhost:8000`.

> SQLite is for local development only — the atomic job-claim query
> (`SELECT ... FOR UPDATE SKIP LOCKED`) is a Postgres feature; on SQLite it
> falls back to a plain locking read, which is fine for a single process but
> won't give you true multi-worker safety the way Postgres does.

## Running multiple workers locally

```bash
WORKER_ID=worker-1 python -m worker.worker_main
WORKER_ID=worker-2 python -m worker.worker_main
```

(On Windows PowerShell: `$env:WORKER_ID="worker-1"; python -m worker.worker_main`)

Both poll the same queues; the `FOR UPDATE SKIP LOCKED` claim guarantees each
job is picked up by exactly one of them.

## Running tests

```bash
cd backend
pytest tests/ -v
```

Tests spin up an isolated in-memory SQLite database per run — no external
services required.

## Deploying (e.g. to Render)

- **API + worker**: any container host (Render, Railway, Fly.io, ECS). Use
  `Dockerfile` for the API and `Dockerfile.worker` for worker replicas — scale
  worker replicas independently of the API.
- **Database**: managed Postgres (Render/Railway/RDS/Supabase). Set
  `DATABASE_URL` accordingly.
- **Frontend**: any static host (Render static site, Netlify, Vercel, GitHub
  Pages, S3). It's plain HTML/CSS/JS — just needs the API's public URL
  entered in the "API base URL" field on first sign-in (stored in the
  browser's localStorage after that).
- Set a strong `JWT_SECRET_KEY` in production — the `.env.example` default is
  not secure.

### Render specifics
1. Create a **Postgres** instance on Render, copy its internal connection string.
2. Create a **Web Service** from `backend/`, Dockerfile = `Dockerfile`, set
   `DATABASE_URL` (use `postgresql+asyncpg://...`, not the default
   `postgresql://...` Render gives you — swap the scheme) and `JWT_SECRET_KEY`.
3. Create a **Background Worker** (or a second Web Service with no public
   port) from `backend/`, Dockerfile = `Dockerfile.worker`, same env vars plus
   a unique `WORKER_ID`. Add more worker services to scale horizontally.
4. Create a **Static Site** from `frontend/` with no build command, publish
   directory `.`.
5. On first visit to the static site, enter the API web service's public URL
   in the "API base URL" field before signing in.

## Adding real job handlers

Worker execution logic lives in `backend/worker/handlers.py`. Add a new
`async def` function, register it in the `HANDLERS` dict, and jobs created
with that `handler` name will run it:

```python
async def send_email(payload: dict) -> dict:
    # payload = {"to": "...", "subject": "...", "body": "..."}
    ...
    return {"sent": True}

HANDLERS["send_email"] = send_email
```

## Notes on schema migrations

For simplicity this project creates tables via `Base.metadata.create_all` on
startup (see `app/database.py`). For a real production rollout, swap this for
[Alembic](https://alembic.sqlalchemy.org/) migrations so schema changes are
versioned — the `docs/DESIGN_DECISIONS.md` file explains the trade-off.

See `docs/ARCHITECTURE.md` for the system diagram, `docs/API.md` for the full
endpoint reference, and `docs/DESIGN_DECISIONS.md` for the reasoning behind
the schema and concurrency model (useful content for your presentation deck).