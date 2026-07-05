# Architecture

## Components

```
                 ┌─────────────┐
                 │  Dashboard  │  (static HTML/CSS/JS)
                 └──────┬──────┘
                        │ REST + JWT
                 ┌──────▼──────┐          ┌──────────────┐
                 │  FastAPI    │◄────────►│  PostgreSQL  │
                 │  API server │          │  (jobs table │
                 │ + scheduler │          │   is the     │
                 │   loop      │          │   source of  │
                 └─────────────┘          │   truth)     │
                                          └──────▲───────┘
                        ┌─────────────────────────┼─────────────────────────┐
                        │                         │                         │
                 ┌──────┴──────┐          ┌──────┴──────┐          ┌──────┴──────┐
                 │  Worker 1   │          │  Worker 2   │          │  Worker N   │
                 │ (poll+claim │          │ (poll+claim │          │ (poll+claim │
                 │  +execute)  │          │  +execute)  │          │  +execute)  │
                 └─────────────┘          └─────────────┘          └─────────────┘
```

- **API server** — auth, CRUD for projects/queues/jobs, dashboard metrics.
  Also runs a lightweight in-process **scheduler loop** (asyncio task) that
  promotes due `SCHEDULED` jobs to `QUEUED` and spawns new job instances from
  `RECURRING` cron templates.
- **Workers** — independent processes (can run on separate machines/containers).
  Each polls the `jobs` table, atomically claims a batch via
  `SELECT ... FOR UPDATE SKIP LOCKED`, executes them concurrently up to a
  configurable concurrency limit, and reports heartbeats.
- **Database** — single source of truth for coordination. No message broker
  (Redis/RabbitMQ/SQS) is used; row-level locking on Postgres does the job of
  a queue broker, which keeps the infrastructure footprint small at the cost
  of higher DB load at very large scale (see `DESIGN_DECISIONS.md`).

## Job lifecycle

```
 SCHEDULED ──(due)──► QUEUED ──(claimed)──► CLAIMED ──► RUNNING ─┬─► COMPLETED
     ▲                                                            │
     └────────────────(retry, backoff delay)───────────────────── ├─► FAILED (has retries left → back to SCHEDULED)
                                                                   └─► DEAD_LETTER (retries exhausted)

  (any state before terminal) ──(user cancels)──► CANCELLED
```

`RECURRING` template jobs never enter this pipeline themselves — the
scheduler loop spawns fresh `IMMEDIATE` job instances from them on each cron
tick.

## Concurrency safety

Two (or more) worker processes polling the same table concurrently must never
execute the same job twice. This is guaranteed by:

1. `SELECT ... FOR UPDATE SKIP LOCKED` — each worker's claim query locks the
   rows it selects; other workers' concurrent claim queries skip locked rows
   instead of blocking, so there's no overlap.
2. Claiming and transitioning `QUEUED → CLAIMED` happens in the same
   transaction as the row lock, so no window exists where two workers see a
   job as claimable.

On SQLite (dev-only), `SKIP LOCKED` isn't supported; the query falls back to
a plain locking read, which is safe for a single worker process but doesn't
give the same multi-worker guarantee — hence the recommendation to use
Postgres for anything beyond local development.

## Retry & backoff

Each queue has a `RetryPolicy` (`fixed` / `linear` / `exponential`), a
`max_retries`, and delay bounds. When a job's handler raises, the worker:

1. Records a `JobExecution` row with the failure.
2. If `attempt_count > max_retries`, moves the job to `DEAD_LETTER` and
   writes a `DeadLetterEntry` snapshot.
3. Otherwise computes the next backoff delay and moves the job back to
   `SCHEDULED` with `scheduled_at = now + delay`, to be picked up again once
   due.
