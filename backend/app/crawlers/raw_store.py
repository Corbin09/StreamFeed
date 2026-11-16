"""Persists fetched pages so runs can be re-parsed without touching the network.

Layout: <data_dir>/raw/<source_type>/<YYYY-MM-DD>/<run_id>/<NNNN>.html|json + manifest.json
`crawl_runs.raw_dir` stores the run folder relative to data_dir, so the same value works
on the host and inside containers.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

from app.crawlers.base import RawPage

MANIFEST = "manifest.json"


class RawRunWriter:
    """Writes the pages of one run. The manifest is rewritten after every page so a
    crashed run still leaves a readable folder."""

    def __init__(self, base_dir: Path, relative_dir: str) -> None:
        self.relative_dir = relative_dir
        self.path = base_dir / relative_dir
        self.path.mkdir(parents=True, exist_ok=True)
        self._entries: list[dict[str, str]] = []

    def save(self, page: RawPage) -> Path:
        name = f"{len(self._entries) + 1:04d}.{'json' if page.is_json else 'html'}"
        target = self.path / name
        target.write_text(page.body, encoding="utf-8")
        self._entries.append(
            {
                "file": name,
                "url": page.url,
                "content_type": page.content_type,
                "fetched_at": page.fetched_at.isoformat(),
            }
        )
        (self.path / MANIFEST).write_text(
            json.dumps({"pages": self._entries}, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return target

    @property
    def page_count(self) -> int:
        return len(self._entries)


class RawStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def open_run(self, source_type: str, run_id: int, day: date | None = None) -> RawRunWriter:
        day = day or datetime.now(UTC).date()
        relative = f"raw/{source_type}/{day.isoformat()}/{run_id}"
        return RawRunWriter(self.data_dir, relative)

    def load_run(self, relative_dir: str) -> Iterator[RawPage]:
        folder = self.data_dir / relative_dir
        manifest_path = folder / MANIFEST
        if not manifest_path.exists():
            raise FileNotFoundError(f"No raw pages saved at {folder}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest["pages"]:
            yield RawPage(
                url=entry["url"],
                body=(folder / entry["file"]).read_text(encoding="utf-8"),
                content_type=entry["content_type"],
                fetched_at=datetime.fromisoformat(entry["fetched_at"]),
            )
