import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_user
from app.models import DeadLetterEntry, Job, JobLog, JobStatus, JobType, Queue, User
from app.schemas import JobCreate, JobDetailOut, JobOut, PaginatedResponse

router = APIRouter(prefix="/api/queues/{queue_id}/jobs", tags=["jobs"])


async def _get_queue_or_404(queue_id: str, db: AsyncSession) -> Queue:
    result = await db.execute(select(Queue).where(Queue.id == queue_id))
    queue = result.scalar_one_or_none()
    if queue is None:
        raise HTTPException(status_code=404, detail="Queue not found")
    return queue


def _initial_status(payload: JobCreate) -> JobStatus:
    if payload.job_type == JobType.DELAYED:
        if payload.scheduled_at is None:
            raise HTTPException(status_code=422, detail="scheduled_at is required for delayed jobs")
        return JobStatus.SCHEDULED
    if payload.job_type == JobType.SCHEDULED:
        if payload.scheduled_at is None:
            raise HTTPException(status_code=422, detail="scheduled_at is required for scheduled jobs")
        return JobStatus.SCHEDULED
    if payload.job_type == JobType.RECURRING:
        if not payload.cron_expression:
            raise HTTPException(status_code=422, detail="cron_expression is required for recurring jobs")
        # The recurring "job" itself is a template and never runs directly;
        # the scheduler loop spawns IMMEDIATE child jobs from it.
        return JobStatus.SCHEDULED
    return JobStatus.QUEUED


@router.post("", response_model=list[JobOut] | JobOut, status_code=201)
async def create_job(
    queue_id: str,
    payload: JobCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_queue_or_404(queue_id, db)
    status_ = _initial_status(payload)

    if payload.job_type == JobType.BATCH:
        items = payload.batch_items or [payload.payload] * (payload.batch_size or 1)
        batch_id = str(uuid.uuid4())
        jobs = [
            Job(
                queue_id=queue_id,
                job_type=JobType.BATCH,
                status=JobStatus.QUEUED,
                handler=payload.handler,
                payload=item,
                priority=payload.priority,
                batch_id=batch_id,
                max_retries=payload.max_retries,
                idempotency_key=payload.idempotency_key,
            )
            for item in items
        ]
        db.add_all(jobs)
        await db.commit()
        for j in jobs:
            await db.refresh(j)
        return jobs

    job = Job(
        queue_id=queue_id,
        job_type=payload.job_type,
        status=status_,
        handler=payload.handler,
        payload=payload.payload,
        priority=payload.priority,
        scheduled_at=payload.scheduled_at,
        cron_expression=payload.cron_expression,
        max_retries=payload.max_retries,
        idempotency_key=payload.idempotency_key,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


@router.get("", response_model=PaginatedResponse)
async def list_jobs(
    queue_id: str,
    status_filter: JobStatus | None = Query(None, alias="status"),
    handler: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_queue_or_404(queue_id, db)

    stmt = select(Job).where(Job.queue_id == queue_id)
    count_stmt = select(func.count(Job.id)).where(Job.queue_id == queue_id)

    if status_filter is not None:
        stmt = stmt.where(Job.status == status_filter)
        count_stmt = count_stmt.where(Job.status == status_filter)
    if handler is not None:
        stmt = stmt.where(Job.handler == handler)
        count_stmt = count_stmt.where(Job.handler == handler)

    total = (await db.execute(count_stmt)).scalar_one()

    stmt = stmt.order_by(Job.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    jobs = result.scalars().all()

    return PaginatedResponse(items=[JobOut.model_validate(j) for j in jobs], total=total, page=page, page_size=page_size)


@router.get("/dead-letter/list", response_model=list[JobDetailOut])
async def list_dead_letter_jobs(
    queue_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """List all jobs currently sitting in this queue's dead-letter queue."""
    result = await db.execute(
        select(Job)
        .options(selectinload(Job.executions), selectinload(Job.logs))
        .where(Job.queue_id == queue_id, Job.status == JobStatus.DEAD_LETTER)
        .order_by(Job.finished_at.desc())
    )
    return result.scalars().all()


@router.get("/{job_id}", response_model=JobDetailOut)
async def get_job(
    queue_id: str, job_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    result = await db.execute(
        select(Job)
        .options(selectinload(Job.executions), selectinload(Job.logs))
        .where(Job.id == job_id, Job.queue_id == queue_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/{job_id}/retry", response_model=JobOut)
async def retry_job(
    queue_id: str, job_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """Manually requeue a FAILED or DEAD_LETTER job (e.g. from the dashboard)."""
    result = await db.execute(
        select(Job).options(selectinload(Job.dead_letter_entry)).where(Job.id == job_id, Job.queue_id == queue_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.FAILED, JobStatus.DEAD_LETTER):
        raise HTTPException(status_code=400, detail="Only failed or dead-letter jobs can be retried")

    job.status = JobStatus.QUEUED
    job.claimed_by_worker_id = None
    job.claimed_at = None
    job.started_at = None
    job.finished_at = None

    if job.dead_letter_entry is not None:
        job.dead_letter_entry.reprocessed = True

    db.add(JobLog(job_id=job.id, level="INFO", message=f"Manually requeued by user {current_user.email}"))
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(
    queue_id: str, job_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    result = await db.execute(select(Job).where(Job.id == job_id, Job.queue_id == queue_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in (JobStatus.COMPLETED, JobStatus.CANCELLED):
        raise HTTPException(status_code=400, detail=f"Cannot cancel a job that is already {job.status.value}")

    job.status = JobStatus.CANCELLED
    job.finished_at = datetime.now(timezone.utc)
    db.add(JobLog(job_id=job.id, level="INFO", message=f"Cancelled by user {current_user.email}"))
    await db.commit()
    await db.refresh(job)
    return job