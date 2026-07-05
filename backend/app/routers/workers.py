from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User, Worker, WorkerHeartbeat
from app.schemas import WorkerOut

router = APIRouter(prefix="/api/workers", tags=["workers"])


class HeartbeatIn(BaseModel):
    active_jobs: int = 0
    cpu_percent: float | None = None
    memory_mb: float | None = None
    hostname: str | None = None
    concurrency: int | None = None


@router.post("/{worker_id}/heartbeat", status_code=204)
async def heartbeat(worker_id: str, payload: HeartbeatIn, db: AsyncSession = Depends(get_db)):
    """Called by worker processes themselves — no end-user auth required,
    workers authenticate via network/deployment boundary (e.g. internal
    network, mTLS) in a production setup."""
    now = datetime.now(timezone.utc)
    result = await db.execute(select(Worker).where(Worker.id == worker_id))
    worker = result.scalar_one_or_none()
    if worker is None:
        worker = Worker(
            id=worker_id,
            hostname=payload.hostname,
            concurrency=payload.concurrency or 4,
            last_seen_at=now,
        )
        db.add(worker)
    else:
        worker.last_seen_at = now
        worker.status = "online"
        if payload.hostname:
            worker.hostname = payload.hostname
        if payload.concurrency:
            worker.concurrency = payload.concurrency

    db.add(
        WorkerHeartbeat(
            worker_id=worker_id,
            active_jobs=payload.active_jobs,
            cpu_percent=payload.cpu_percent,
            memory_mb=payload.memory_mb,
        )
    )
    await db.commit()


@router.get("", response_model=list[WorkerOut])
async def list_workers(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Worker))
    workers = result.scalars().all()
    # Mark anything not seen in 3x the expected heartbeat window as offline.
    stale_cutoff = datetime.now(timezone.utc) - timedelta(seconds=30)
    for w in workers:
        if w.last_seen_at is not None and w.last_seen_at < stale_cutoff:
            w.status = "offline"
    return workers