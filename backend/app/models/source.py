"""A configured crawl source (e.g. one ITviec search, one set of Facebook groups)."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SourceType(StrEnum):
    ITVIEC = "itviec"
    LINKEDIN = "linkedin"
    FACEBOOK = "facebook"


class SourceStatus(StrEnum):
    IDLE = "idle"  # never run yet
    OK = "ok"
    NEEDS_LOGIN = "needs_login"  # login/checkpoint page detected; paused until manual login
    ERROR = "error"


def _in(values: type[StrEnum]) -> str:
    return ", ".join(f"'{v.value}'" for v in values)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(SourceStatus)})", name="status"),
        CheckConstraint("interval_minutes > 0", name="interval_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Free-form string (not a DB enum) so new crawler plugins need no migration.
    type: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    interval_minutes: Mapped[int] = mapped_column(Integer, default=120, server_default="120")
    status: Mapped[str] = mapped_column(
        String(16), default=SourceStatus.IDLE, server_default=SourceStatus.IDLE.value
    )
    # Drives exponential backoff of the effective interval; reset on success.
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
