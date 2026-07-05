# API Reference

Base URL: `http://localhost:8000` (or your deployed API URL). Interactive
Swagger docs are always available at `/docs` once the API is running.

All endpoints except `/api/auth/register`, `/api/auth/login`, `/api/health`,
and `/api/workers/{id}/heartbeat` require an `Authorization: Bearer <token>`
header.

## Auth

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/register` | Create a user + a new organization, return the user |
| POST | `/api/auth/login` | OAuth2 password flow (form-encoded `username`/`password`) → JWT |

## Projects

| Method | Path | Description |
|---|---|---|
| POST | `/api/projects` | Create a project in the caller's organization |
| GET | `/api/projects` | List projects in the caller's organization |
| GET | `/api/projects/{project_id}` | Get one project |
| DELETE | `/api/projects/{project_id}` | Delete a project (cascades to queues/jobs) |

## Queues

| Method | Path | Description |
|---|---|---|
| POST | `/api/projects/{project_id}/queues` | Create a queue + its retry policy |
| GET | `/api/projects/{project_id}/queues` | List queues in a project |
| PATCH | `/api/projects/{project_id}/queues/{queue_id}` | Update priority/concurrency/pause state |
| POST | `/api/projects/{project_id}/queues/{queue_id}/pause` | Pause (stop claiming new jobs) |
| POST | `/api/projects/{project_id}/queues/{queue_id}/resume` | Resume |
| GET | `/api/projects/{project_id}/queues/{queue_id}/stats` | Counts per job status |
| DELETE | `/api/projects/{project_id}/queues/{queue_id}` | Delete a queue (cascades to jobs) |

## Jobs

| Method | Path | Description |
|---|---|---|
| POST | `/api/queues/{queue_id}/jobs` | Create a job. `job_type` = `immediate` \| `delayed` \| `scheduled` \| `recurring` \| `batch` |
| GET | `/api/queues/{queue_id}/jobs` | Paginated list, filter by `status` / `handler` |
| GET | `/api/queues/{queue_id}/jobs/dead-letter/list` | All dead-lettered jobs in this queue |
| GET | `/api/queues/{queue_id}/jobs/{job_id}` | Full detail incl. execution history + logs |
| POST | `/api/queues/{queue_id}/jobs/{job_id}/retry` | Manually requeue a `failed`/`dead_letter` job |
| POST | `/api/queues/{queue_id}/jobs/{job_id}/cancel` | Cancel a non-terminal job |

### Job creation payload

```json
{
  "handler": "flaky",
  "payload": {"failure_rate": 0.2},
  "job_type": "immediate",
  "priority": 0,
  "scheduled_at": null,
  "cron_expression": null,
  "max_retries": null,
  "idempotency_key": null,
  "batch_items": null,
  "batch_size": null
}
```

- `delayed` / `scheduled` require `scheduled_at` (ISO datetime).
- `recurring` requires `cron_expression` (standard 5-field cron).
- `batch` uses `batch_items` (explicit list of payloads) or replicates
  `payload` `batch_size` times; returns a list of created jobs sharing a
  `batch_id`.

## Workers

| Method | Path | Description |
|---|---|---|
| POST | `/api/workers/{worker_id}/heartbeat` | Called by worker processes themselves, no user auth |
| GET | `/api/workers` | List all known workers with online/offline status |

## Dashboard

| Method | Path | Description |
|---|---|---|
| GET | `/api/dashboard/health` | Aggregate counts: queued/running/completed(1h)/failed(1h)/dead-letter/workers online |