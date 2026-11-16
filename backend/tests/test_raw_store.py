import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.crawlers.base import RawPage
from app.crawlers.raw_store import RawStore


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    writer = store.open_run("itviec", run_id=42, day=date(2026, 11, 16))
    assert writer.relative_dir == "raw/itviec/2026-11-16/42"

    html = RawPage(url="https://a/1", body="<p>Lập trình viên</p>")
    data = RawPage(
        url="https://a/api",
        body='{"x": 1}',
        content_type="application/json",
        fetched_at=datetime(2026, 11, 16, 8, 0, tzinfo=UTC),
    )
    assert writer.save(html).name == "0001.html"
    assert writer.save(data).name == "0002.json"

    manifest = json.loads((tmp_path / writer.relative_dir / "manifest.json").read_text())
    assert [e["file"] for e in manifest["pages"]] == ["0001.html", "0002.json"]

    loaded = list(store.load_run(writer.relative_dir))
    assert [p.url for p in loaded] == ["https://a/1", "https://a/api"]
    assert loaded[0].body == "<p>Lập trình viên</p>"
    assert loaded[1] == data


def test_load_missing_run_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list(RawStore(tmp_path).load_run("raw/x/2026-01-01/1"))
