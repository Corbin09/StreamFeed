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

    # Worker: idle poll interval for queued runs and due sources; retention (§15).
    worker_poll_seconds: int = 15
    # Local time zone for daily run windows (sources.config.daily_window).
    timezone: str = "Asia/Ho_Chi_Minh"
    # Browser for BrowserFetcher and the login CLI. BROWSER_CHANNEL=chrome uses the installed
    # Google Chrome stable (the worker image sets it); empty uses Playwright's bundled
    # Chromium. BROWSER_EXECUTABLE_PATH points at any other Chromium-based binary (e.g. Brave
    # or Chrome on the host for the headed login).
    browser_channel: str | None = None
    browser_executable_path: str | None = None
    raw_retention_days: int = 30
    run_retention_days: int = 90

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
