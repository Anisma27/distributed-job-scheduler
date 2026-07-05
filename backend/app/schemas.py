from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import JobStatus, JobType, RetryStrategyType

# ==================================================================== Auth


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str
    organization_name: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str
    is_active: bool


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ==================================================================== Projects


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    created_at: datetime


# ==================================================================== Queues / retry policy


class RetryPolicyCreate(BaseModel):
    strategy: RetryStrategyType = RetryStrategyType.EXPONENTIAL
    max_retries: int = 3
    base_delay_seconds: int = 5
    max_delay_seconds: int = 3600


class RetryPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    strategy: RetryStrategyType
    max_retries: int
    base_delay_seconds: int
    max_delay_seconds: int


class QueueCreate(BaseModel):
    name: str
    priority: int = 0
    concurrency_limit: int = 5
    retry_policy: RetryPolicyCreate = Field(default_factory=RetryPolicyCreate)


class QueueUpdate(BaseModel):
    priority: int | None = None
    concurrency_limit: int | None = None
    is_paused: bool | None = None


class QueueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    priority: int
    concurrency_limit: int
    is_paused: bool
    retry_policy: RetryPolicyOut | None = None


class QueueStats(BaseModel):
    queue_id: str
    queued: int = 0
    claimed: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    dead_letter: int = 0
    scheduled: int = 0


# ==================================================================== Jobs


class JobCreate(BaseModel):
    handler: str
    payload: dict[str, Any] = Field(default_factory=dict)
    job_type: JobType = JobType.IMMEDIATE
    priority: int = 0
    scheduled_at: datetime | None = None
    cron_expression: str | None = None
    max_retries: int | None = None
    idempotency_key: str | None = None
    batch_items: list[dict[str, Any]] | None = None
    batch_size: int | None = None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    queue_id: str
    job_type: JobType
    status: JobStatus
    handler: str
    payload: dict[str, Any]
    priority: int
    batch_id: str | None = None
    scheduled_at: datetime | None = None
    cron_expression: str | None = None
    attempt_count: int
    max_retries: int | None = None
    claimed_by_worker_id: str | None = None
    claimed_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class JobExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    attempt_number: int
    worker_id: str
    status: JobStatus
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    result: dict[str, Any] | None = None
    error_message: str | None = None


class JobLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    level: str
    message: str
    created_at: datetime


class JobDetailOut(JobOut):
    executions: list[JobExecutionOut] = Field(default_factory=list)
    logs: list[JobLogOut] = Field(default_factory=list)


class PaginatedResponse(BaseModel):
    items: list[JobOut]
    total: int
    page: int
    page_size: int


# ==================================================================== Workers


class WorkerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    hostname: str | None = None
    concurrency: int
    status: str
    last_seen_at: datetime | None = None


# ==================================================================== Dashboard


class SystemHealthOut(BaseModel):
    total_queues: int
    total_workers_online: int
    jobs_queued: int
    jobs_running: int
    jobs_completed_last_hour: int
    jobs_failed_last_hour: int
    dead_letter_count: int