"""One execution of a crawler against a source, for monitoring."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CrawlRunStatus(StrEnum):
    QUEUED = "queued"  # manual "Run now" request waiting for the worker
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    NEEDS_LOGIN = "needs_login"


class CrawlRunTrigger(StrEnum):
    SCHEDULE = "schedule"
    MANUAL = "manual"


class CrawlRun(Base):
    __tablename__ = "crawl_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s.value}'" for s in CrawlRunStatus) + ")",
            name="status",
        ),
        CheckConstraint(
            "trigger IN (" + ", ".join(f"'{t.value}'" for t in CrawlRunTrigger) + ")",
            name="trigger",
        ),
        Index("ix_crawl_runs_queued", "started_at", postgresql_where=text("status = 'queued'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(
        String(16), default=CrawlRunStatus.RUNNING, server_default=CrawlRunStatus.RUNNING.value
    )
    trigger: Mapped[str] = mapped_column(
        String(16),
        default=CrawlRunTrigger.SCHEDULE,
        server_default=CrawlRunTrigger.SCHEDULE.value,
    )
    # Folder of saved raw pages, relative to settings.data_dir (portable host <-> container).
    raw_dir: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    inserted: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
