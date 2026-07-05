from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import DeadLetterEntry, Job, JobStatus, Queue, User, Worker
from app.schemas import SystemHealthOut

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/health", response_model=SystemHealthOut)
async def system_health(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)

    total_queues = (await db.execute(select(func.count(Queue.id)))).scalar_one()

    online_cutoff = datetime.now(timezone.utc) - timedelta(seconds=30)
    total_workers_online = (
        await db.execute(select(func.count(Worker.id)).where(Worker.last_seen_at >= online_cutoff))
    ).scalar_one()

    jobs_queued = (
        await db.execute(select(func.count(Job.id)).where(Job.status == JobStatus.QUEUED))
    ).scalar_one()
    jobs_running = (
        await db.execute(select(func.count(Job.id)).where(Job.status == JobStatus.RUNNING))
    ).scalar_one()
    jobs_completed_last_hour = (
        await db.execute(
            select(func.count(Job.id)).where(Job.status == JobStatus.COMPLETED, Job.finished_at >= one_hour_ago)
        )
    ).scalar_one()
    jobs_failed_last_hour = (
        await db.execute(
            select(func.count(Job.id)).where(Job.status == JobStatus.FAILED, Job.updated_at >= one_hour_ago)
        )
    ).scalar_one()
    dead_letter_count = (await db.execute(select(func.count(DeadLetterEntry.id)))).scalar_one()

    return SystemHealthOut(
        total_queues=total_queues,
        total_workers_online=total_workers_online,
        jobs_queued=jobs_queued,
        jobs_running=jobs_running,
        jobs_completed_last_hour=jobs_completed_last_hour,
        jobs_failed_last_hour=jobs_failed_last_hour,
        dead_letter_count=dead_letter_count,
    )