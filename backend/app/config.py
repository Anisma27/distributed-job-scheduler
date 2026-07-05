from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database ---
    DATABASE_URL: str = "sqlite+aiosqlite:///./scheduler.db"

    # --- Auth ---
    JWT_SECRET_KEY: str = "dev-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24h

    # --- Scheduler loop (promotes delayed/scheduled jobs, spawns cron jobs) ---
    SCHEDULER_TICK_SECONDS: float = 2.0

    # --- Worker process ---
    API_BASE_URL: str = "http://localhost:8000"
    WORKER_ID: str = "worker-local"
    WORKER_CONCURRENCY: int = 4
    WORKER_POLL_INTERVAL_SECONDS: float = 1.0
    WORKER_HEARTBEAT_INTERVAL_SECONDS: float = 5.0


@lru_cache
def get_settings() -> Settings:
    return Settings()