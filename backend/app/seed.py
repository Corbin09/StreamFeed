"""Load source definitions from data/seeds/sources.yaml into the `sources` table.

Usage: python -m app.seed [path/to/sources.yaml]

Sources are matched by name. New sources are inserted; existing ones get their
type/config/interval updated. `enabled` and `status` of existing sources are left
untouched so changes made at runtime (e.g. in the admin UI) are not overwritten.
"""

import logging
import sys
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models import Source

logger = logging.getLogger(__name__)


def load_seed_file(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("sources") or []


def seed_sources(session: Session, entries: list[dict[str, Any]]) -> tuple[int, int]:
    """Upsert sources by name. Returns (inserted, updated)."""
    default_interval = get_settings().default_crawl_interval_minutes
    inserted = updated = 0
    for entry in entries:
        source = session.scalar(select(Source).where(Source.name == entry["name"]))
        if source is None:
            session.add(
                Source(
                    type=entry["type"],
                    name=entry["name"],
                    enabled=entry.get("enabled", True),
                    interval_minutes=entry.get("interval_minutes", default_interval),
                    config=entry.get("config") or {},
                )
            )
            inserted += 1
        else:
            source.type = entry["type"]
            source.interval_minutes = entry.get("interval_minutes", source.interval_minutes)
            source.config = entry.get("config") or {}
            updated += 1
    session.commit()
    return inserted, updated


def main() -> None:
    logging.basicConfig(level=get_settings().log_level)
    path = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else get_settings().data_dir / "seeds/sources.yaml"
    )
    with SessionLocal() as session:
        inserted, updated = seed_sources(session, load_seed_file(path))
    logger.info("Seeded sources from %s: %d inserted, %d updated", path, inserted, updated)


if __name__ == "__main__":
    main()
