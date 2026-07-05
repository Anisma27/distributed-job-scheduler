# Design Decisions

## Postgres as the queue broker, no Redis/RabbitMQ/SQS

A dedicated message broker gives you push-based delivery and often better
throughput at very high volume. This project instead uses Postgres row
locking (`SELECT ... FOR UPDATE SKIP LOCKED`) as the coordination mechanism.

Trade-offs accepted:
- **Pro**: one fewer moving part to deploy/operate; the jobs table is also
  naturally the audit log (nothing to reconcile between broker and DB).
  Strong consistency by construction — a job's state never disagrees between
  "what the broker thinks" and "what the DB thinks" because there's only one
  store.
- **Con**: workers poll on an interval (`WORKER_POLL_INTERVAL_SECONDS`)
  rather than receiving push notifications, adding up to one poll interval of
  latency. At very high job volumes, the `jobs` table becomes a write
  hotspot; a real broker would shard/partition this load more naturally.

For this project's scale (a portfolio/demo system, not a high-throughput
production queue), the simplicity trade-off favors Postgres-only.

## `Base.metadata.create_all` instead of Alembic migrations

Tables are created directly from the ORM models on startup. This is fine for
a project where the schema is authored once and iterated on by regenerating
a fresh database, but it has no notion of *versioned, incremental* schema
changes — you can't safely evolve a live production database with data you
need to keep. A real rollout would introduce Alembic so each schema change is
a reviewable, revertible migration script, and CI can verify `alembic upgrade
head` runs cleanly against a copy of production before deploy.

## SQLite fallback for local dev

`DATABASE_URL` can point at SQLite for zero-install local development. The
atomic claim query degrades gracefully (falls back to a plain locking read
when the `FOR UPDATE SKIP LOCKED ... of=Job` clause isn't supported), which
is safe for a single process. This is explicitly not suitable for proving
the multi-worker safety guarantee — that requires Postgres, which is what
`docker-compose.yml` provisions by default.

## JWT with a symmetric secret, no refresh tokens

Access tokens are long-lived (24h default) rather than using a short-lived
access + refresh token pair. This is simpler to implement and reason about
for a project of this scope; a production system handling sensitive data
would shorten the access token lifetime and add refresh token rotation with
revocation support.

## Multi-tenancy via Organization → Project → Queue → Job

Every user belongs to an organization (created automatically at
registration); every project belongs to one organization; queues and jobs
cascade from there. This mirrors how most real SaaS backends structure
tenancy and made it straightforward to scope every list/query to "things
this user's organization owns" via a single `_get_user_org_id` helper.

## Dead-letter queue as a first-class table, not just a status

`DeadLetterEntry` is its own table (not just `Job.status == DEAD_LETTER`) so
that a dead-lettered job keeps a permanent snapshot of the payload and
failure reason at the moment it was dead-lettered, independent of whatever
happens to the live `Job` row afterward (e.g. if it's later manually
retried and re-fails differently). It also gives a natural place to track
`reprocessed` without overloading the job's own status history.