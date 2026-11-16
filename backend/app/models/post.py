"""A normalized post from any source and any category (job, vehicle, ...)."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Category(StrEnum):
    JOB = "job"
    VEHICLE = "vehicle"
    REAL_ESTATE = "real_estate"
    KEYBOARD = "keyboard"
    MOUSE = "mouse"


# Weighted, accent-insensitive document using the language-neutral 'simple' config,
# so Vietnamese and English content are both searchable with or without diacritics.
# f_unaccent is an IMMUTABLE wrapper around unaccent() created in the first migration.
SEARCH_VECTOR_EXPR = (
    "setweight(to_tsvector('simple', f_unaccent(coalesce(title, ''))), 'A') || "
    "setweight(to_tsvector('simple', f_unaccent(coalesce(company, ''))), 'B') || "
    "setweight(to_tsvector('simple', f_unaccent(coalesce(body, ''))), 'C')"
)


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_posts_source_external"),
        Index("ix_posts_category_posted_at", "category", text("posted_at DESC")),
        Index("ix_posts_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_posts_attributes", "attributes", postgresql_using="gin"),
        Index(
            "ix_posts_title_trgm",
            text("f_unaccent(title) gin_trgm_ops"),
            postgresql_using="gin",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    # ID of the post on the source site (or a stable hash of its URL when none exists).
    external_id: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32), default=Category.JOB)

    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="", server_default="")
    language: Mapped[str | None] = mapped_column(String(8))  # 'vi' | 'en' | None
    author: Mapped[str | None] = mapped_column(String(255))  # poster name (e.g. FB user)
    company: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(64), index=True)  # canonical key

    # Salary for jobs, asking price for marketplace listings.
    price_min: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    price_max: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    currency: Mapped[str | None] = mapped_column(String(3))  # ISO 4217: VND, USD

    # Category-specific fields, validated by app/schemas/attributes/<category>.py
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")

    # Cross-source dedup: posts sharing a content_hash point at the earliest one.
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    duplicate_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("posts.id", ondelete="SET NULL"), index=True
    )

    is_saved: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    crawled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Refreshed every time a crawl sees the post again; used to detect expired posts.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed(SEARCH_VECTOR_EXPR, persisted=True)
    )
