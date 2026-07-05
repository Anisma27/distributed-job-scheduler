from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_user
from app.models import Job, JobStatus, Queue, RetryPolicy, User
from app.routers.projects import _get_user_org_id
from app.schemas import QueueCreate, QueueOut, QueueStats, QueueUpdate

router = APIRouter(prefix="/api/projects/{project_id}/queues", tags=["queues"])


async def _get_queue_or_404(queue_id: str, project_id: str, db: AsyncSession) -> Queue:
    result = await db.execute(
        select(Queue)
        .options(selectinload(Queue.retry_policy))
        .where(Queue.id == queue_id, Queue.project_id == project_id)
    )
    queue = result.scalar_one_or_none()
    if queue is None:
        raise HTTPException(status_code=404, detail="Queue not found")
    return queue


@router.post("", response_model=QueueOut, status_code=201)
async def create_queue(
    project_id: str,
    payload: QueueCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_user_org_id(current_user, db)  # ensures authenticated + in an org

    queue = Queue(
        project_id=project_id,
        name=payload.name,
        priority=payload.priority,
        concurrency_limit=payload.concurrency_limit,
    )
    db.add(queue)
    await db.flush()

    retry_policy = RetryPolicy(
        queue_id=queue.id,
        strategy=payload.retry_policy.strategy,
        max_retries=payload.retry_policy.max_retries,
        base_delay_seconds=payload.retry_policy.base_delay_seconds,
        max_delay_seconds=payload.retry_policy.max_delay_seconds,
    )
    db.add(retry_policy)
    await db.commit()

    return await _get_queue_or_404(queue.id, project_id, db)


@router.get("", response_model=list[QueueOut])
async def list_queues(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Queue).options(selectinload(Queue.retry_policy)).where(Queue.project_id == project_id)
    )
    return result.scalars().all()


@router.patch("/{queue_id}", response_model=QueueOut)
async def update_queue(
    project_id: str,
    queue_id: str,
    payload: QueueUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    queue = await _get_queue_or_404(queue_id, project_id, db)
    if payload.priority is not None:
        queue.priority = payload.priority
    if payload.concurrency_limit is not None:
        queue.concurrency_limit = payload.concurrency_limit
    if payload.is_paused is not None:
        queue.is_paused = payload.is_paused
    await db.commit()
    return await _get_queue_or_404(queue_id, project_id, db)


@router.post("/{queue_id}/pause", response_model=QueueOut)
async def pause_queue(
    project_id: str, queue_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    queue = await _get_queue_or_404(queue_id, project_id, db)
    queue.is_paused = True
    await db.commit()
    return await _get_queue_or_404(queue_id, project_id, db)


@router.post("/{queue_id}/resume", response_model=QueueOut)
async def resume_queue(
    project_id: str, queue_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    queue = await _get_queue_or_404(queue_id, project_id, db)
    queue.is_paused = False
    await db.commit()
    return await _get_queue_or_404(queue_id, project_id, db)


@router.get("/{queue_id}/stats", response_model=QueueStats)
async def queue_stats(
    project_id: str, queue_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    await _get_queue_or_404(queue_id, project_id, db)

    result = await db.execute(
        select(Job.status, func.count(Job.id)).where(Job.queue_id == queue_id).group_by(Job.status)
    )
    counts = {status.value if hasattr(status, "value") else status: count for status, count in result.all()}

    return QueueStats(
        queue_id=queue_id,
        queued=counts.get(JobStatus.QUEUED.value, 0),
        claimed=counts.get(JobStatus.CLAIMED.value, 0),
        running=counts.get(JobStatus.RUNNING.value, 0),
        completed=counts.get(JobStatus.COMPLETED.value, 0),
        failed=counts.get(JobStatus.FAILED.value, 0),
        dead_letter=counts.get(JobStatus.DEAD_LETTER.value, 0),
        scheduled=counts.get(JobStatus.SCHEDULED.value, 0),
    )


@router.delete("/{queue_id}", status_code=204)
async def delete_queue(
    project_id: str, queue_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    queue = await _get_queue_or_404(queue_id, project_id, db)
    await db.delete(queue)
    await db.commit()