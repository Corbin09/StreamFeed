"""Application settings loaded from environment variables / .env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "postgresql+psycopg://streamfeed:streamfeed@localhost:5433/streamfeed"
    data_dir: Path = Path("../data")
    log_level: str = "INFO"
    # Comma-separated list, e.g. "http://localhost:3000,http://127.0.0.1:3000"
    cors_origins: str = "http://localhost:3000"

    default_crawl_interval_minutes: int = 120
    crawl_jitter_minutes: int = 15

    # Fixed rate used only to compare VND and USD salaries/prices at query time.
    usd_vnd_rate: float = 26_000
    # Posts not seen by a crawl for this long are hidden from the default feed.
    stale_after_days: int = 30
    # /health/worker reports the worker as down when its heartbeat is older than this.
    worker_heartbeat_max_age_seconds: int = 120

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
