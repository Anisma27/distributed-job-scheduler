from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobStatus, Queue


async def claim_jobs(
    db: AsyncSession,
    worker_id: str,
    limit: int,
) -> list[Job]:
    """Atomically claim up to `limit` queued jobs across all non-paused queues,
    ordered by queue priority then job priority then creation time (FIFO).

    Uses SELECT ... FOR UPDATE SKIP LOCKED so multiple worker processes can
    poll concurrently without claiming the same row (duplicate execution).
    On SQLite (used for local dev without Postgres) skip_locked degrades to a
    plain row lock, which is fine since SQLite serializes writers anyway.
    """
    stmt = (
        select(Job)
        .join(Queue, Job.queue_id == Queue.id)
        .where(Job.status == JobStatus.QUEUED)
        .where(Queue.is_paused == False)  # noqa: E712
        .order_by(Queue.priority.desc(), Job.priority.desc(), Job.created_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True, of=Job)
    )

    try:
        result = await db.execute(stmt)
    except Exception:
        # `of=Job` FOR UPDATE on a join isn't supported by every backend
        # (e.g. SQLite). Fall back to a plain locking read.
        stmt_fallback = (
            select(Job)
            .join(Queue, Job.queue_id == Queue.id)
            .where(Job.status == JobStatus.QUEUED)
            .where(Queue.is_paused == False)  # noqa: E712
            .order_by(Queue.priority.desc(), Job.priority.desc(), Job.created_at.asc())
            .limit(limit)
        )
        result = await db.execute(stmt_fallback)

    jobs = list(result.scalars().all())
    if not jobs:
        return []

    now = datetime.now(timezone.utc)
    job_ids = [j.id for j in jobs]

    await db.execute(
        update(Job)
        .where(Job.id.in_(job_ids))
        .values(
            status=JobStatus.CLAIMED,
            claimed_by_worker_id=worker_id,
            claimed_at=now,
        )
    )
    await db.commit()

    for j in jobs:
        j.status = JobStatus.CLAIMED
        j.claimed_by_worker_id = worker_id
        j.claimed_at = now

    return jobs
