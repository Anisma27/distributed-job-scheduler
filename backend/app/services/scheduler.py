import asyncio
import logging
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select, update

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import Job, JobStatus, JobType

logger = logging.getLogger("scheduler")
settings = get_settings()


async def _promote_due_jobs() -> None:
    """Move SCHEDULED jobs whose scheduled_at has passed into QUEUED."""
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        await db.execute(
            update(Job)
            .where(Job.status == JobStatus.SCHEDULED)
            .where(Job.scheduled_at.is_not(None))
            .where(Job.scheduled_at <= now)
            .values(status=JobStatus.QUEUED)
        )
        await db.commit()


async def _spawn_recurring_jobs() -> None:
    """For each RECURRING template job, check whether its cron schedule is
    due and, if so, create a fresh QUEUED job instance (the template itself
    never runs directly)."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Job).where(Job.job_type == JobType.RECURRING).where(Job.cron_expression.is_not(None))
        )
        templates = result.scalars().all()
        now = datetime.now(timezone.utc)

        for template in templates:
            try:
                cron = croniter(template.cron_expression, template.updated_at)
                next_run = cron.get_next(datetime)
            except Exception:
                logger.warning("Invalid cron expression on job %s: %s", template.id, template.cron_expression)
                continue

            if next_run.tzinfo is None:
                next_run = next_run.replace(tzinfo=timezone.utc)

            if next_run <= now:
                new_job = Job(
                    queue_id=template.queue_id,
                    job_type=JobType.IMMEDIATE,
                    status=JobStatus.QUEUED,
                    handler=template.handler,
                    payload=template.payload,
                    priority=template.priority,
                    max_retries=template.max_retries,
                    parent_recurring_job_id=template.id,
                )
                db.add(new_job)
                template.updated_at = now  # anchors the next croniter calculation

        await db.commit()


async def scheduler_loop(stop_event: asyncio.Event | None = None) -> None:
    """Run forever (or until stop_event is set), ticking on an interval."""
    logger.info("Scheduler loop starting (tick every %ss)", settings.SCHEDULER_TICK_SECONDS)
    while stop_event is None or not stop_event.is_set():
        try:
            await _promote_due_jobs()
            await _spawn_recurring_jobs()
        except Exception:
            logger.exception("Scheduler tick failed")
        await asyncio.sleep(settings.SCHEDULER_TICK_SECONDS)