"""
Standalone worker process for the distributed job scheduler.

Run one or many of these (locally, in separate containers, or on separate
machines) pointed at the same database — they'll coordinate through the
`jobs` table's atomic claim (SELECT ... FOR UPDATE SKIP LOCKED), so no two
workers will ever execute the same job.

Usage:
    WORKER_ID=worker-1 python -m worker.worker_main
    WORKER_ID=worker-2 python -m worker.worker_main
"""
import asyncio
import logging
import signal
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import DeadLetterEntry, Job, JobExecution, JobLog, JobStatus, Queue, RetryPolicy
from app.services.job_claim import claim_jobs
from app.services.retry import compute_backoff_seconds
from worker.handlers import run_handler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("worker")
settings = get_settings()


class GracefulShutdown:
    def __init__(self) -> None:
        self.should_stop = asyncio.Event()

    def request_stop(self, *_args) -> None:
        logger.info("Shutdown signal received — finishing in-flight jobs, no new jobs will be claimed")
        self.should_stop.set()


async def _send_heartbeat(worker_id: str, active_jobs: int) -> None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{settings.API_BASE_URL}/api/workers/{worker_id}/heartbeat",
                json={"active_jobs": active_jobs, "concurrency": settings.WORKER_CONCURRENCY},
            )
    except Exception as exc:  # heartbeat failures shouldn't crash the worker
        logger.warning("Heartbeat failed: %s", exc)


async def _heartbeat_loop(worker_id: str, active_counter: "ActiveJobCounter", stop: GracefulShutdown) -> None:
    while not stop.should_stop.is_set():
        await _send_heartbeat(worker_id, active_counter.count)
        await asyncio.sleep(settings.WORKER_HEARTBEAT_INTERVAL_SECONDS)


class ActiveJobCounter:
    def __init__(self) -> None:
        self.count = 0


async def _execute_job(job: Job, worker_id: str, active_counter: ActiveJobCounter) -> None:
    active_counter.count += 1
    attempt_number = job.attempt_count + 1
    start = time.monotonic()

    async with AsyncSessionLocal() as db:
        db_job = (await db.execute(select(Job).where(Job.id == job.id))).scalar_one()
        db_job.status = JobStatus.RUNNING
        db_job.started_at = datetime.now(timezone.utc)
        db_job.attempt_count = attempt_number
        db.add(JobLog(job_id=job.id, level="INFO", message=f"Attempt {attempt_number} started on {worker_id}"))
        await db.commit()

    result, error = None, None
    try:
        result = await run_handler(job.handler, job.payload)
        final_status = JobStatus.COMPLETED
    except Exception as exc:  # noqa: BLE001 — any handler failure triggers retry logic
        error = str(exc)
        final_status = JobStatus.FAILED

    duration_ms = int((time.monotonic() - start) * 1000)

    async with AsyncSessionLocal() as db:
        db_job = (await db.execute(select(Job).where(Job.id == job.id))).scalar_one()

        db.add(
            JobExecution(
                job_id=job.id,
                attempt_number=attempt_number,
                worker_id=worker_id,
                status=final_status,
                finished_at=datetime.now(timezone.utc),
                duration_ms=duration_ms,
                result=result,
                error_message=error,
            )
        )

        if final_status == JobStatus.COMPLETED:
            db_job.status = JobStatus.COMPLETED
            db_job.finished_at = datetime.now(timezone.utc)
            db.add(JobLog(job_id=job.id, level="INFO", message=f"Attempt {attempt_number} completed successfully"))
        else:
            queue = (await db.execute(select(Queue).where(Queue.id == db_job.queue_id))).scalar_one()
            retry_policy = (
                await db.execute(select(RetryPolicy).where(RetryPolicy.queue_id == queue.id))
            ).scalar_one_or_none()

            max_retries = db_job.max_retries if db_job.max_retries is not None else (
                retry_policy.max_retries if retry_policy else 3
            )

            if attempt_number > max_retries:
                db_job.status = JobStatus.DEAD_LETTER
                db_job.finished_at = datetime.now(timezone.utc)
                db.add(
                    DeadLetterEntry(
                        job_id=job.id,
                        reason=error or "Unknown error",
                        final_payload_snapshot=job.payload,
                    )
                )
                db.add(JobLog(job_id=job.id, level="ERROR", message=f"Moved to dead-letter after {attempt_number} attempts: {error}"))
            else:
                strategy = retry_policy.strategy if retry_policy else None
                base_delay = retry_policy.base_delay_seconds if retry_policy else 5
                max_delay = retry_policy.max_delay_seconds if retry_policy else 3600
                delay = compute_backoff_seconds(strategy, attempt_number, base_delay, max_delay) if strategy else base_delay

                db_job.status = JobStatus.SCHEDULED
                db_job.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                db_job.claimed_by_worker_id = None
                db.add(JobLog(job_id=job.id, level="WARNING", message=f"Attempt {attempt_number} failed, retrying in {delay}s: {error}"))

        await db.commit()

    active_counter.count -= 1


async def worker_loop(stop: GracefulShutdown) -> None:
    worker_id = settings.WORKER_ID
    active_counter = ActiveJobCounter()
    in_flight: set[asyncio.Task] = set()

    heartbeat_task = asyncio.create_task(_heartbeat_loop(worker_id, active_counter, stop))
    logger.info("Worker %s starting (concurrency=%s)", worker_id, settings.WORKER_CONCURRENCY)

    try:
        while not stop.should_stop.is_set():
            available_slots = settings.WORKER_CONCURRENCY - len(in_flight)

            if available_slots > 0:
                async with AsyncSessionLocal() as db:
                    claimed = await claim_jobs(db, worker_id, available_slots)

                for job in claimed:
                    task = asyncio.create_task(_execute_job(job, worker_id, active_counter))
                    in_flight.add(task)
                    task.add_done_callback(in_flight.discard)

            await asyncio.sleep(settings.WORKER_POLL_INTERVAL_SECONDS)

        # Graceful shutdown: stop claiming, wait for in-flight jobs.
        if in_flight:
            logger.info("Waiting for %d in-flight job(s) to finish...", len(in_flight))
            await asyncio.gather(*in_flight, return_exceptions=True)
    finally:
        heartbeat_task.cancel()
        logger.info("Worker %s stopped", worker_id)


def main() -> None:
    stop = GracefulShutdown()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.request_stop)
        except NotImplementedError:
            # add_signal_handler isn't available on Windows event loops;
            # fall back to the default signal module handler.
            signal.signal(sig, stop.request_stop)

    loop.run_until_complete(worker_loop(stop))


if __name__ == "__main__":
    main()