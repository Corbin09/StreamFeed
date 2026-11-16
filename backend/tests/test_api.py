"""REST API tests against the real test database (see conftest.session_factory)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.main import app
from app.models import CrawlRun, CrawlRunStatus, Post, Source, SourceStatus, WorkerHeartbeat
from app.services.search import encode_cursor

NOW = datetime.now(UTC)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def add_source(db: Session, type_: str = "itviec", **kw) -> Source:
    source = Source(type=type_, name=kw.pop("name", f"{type_} source"), **kw)
    db.add(source)
    db.commit()
    return source


_seq = iter(range(1, 100_000))


def add_post(db: Session, source: Source, title: str, **kw) -> Post:
    n = next(_seq)
    kw.setdefault("posted_at", NOW - timedelta(hours=n))
    post = Post(
        source_id=source.id,
        external_id=kw.pop("external_id", f"ext-{n}"),
        url=kw.pop("url", f"https://example.com/{n}"),
        category=kw.pop("category", "job"),
        title=title,
        **kw,
    )
    db.add(post)
    db.commit()
    return post


def ids(response) -> list[int]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


# --- GET /posts ----------------------------------------------------------------------------


def test_feed_is_newest_first_and_cursor_pages_without_gaps(db, client) -> None:
    src = add_source(db)
    posts = [add_post(db, src, f"Job {i}", posted_at=NOW - timedelta(minutes=i)) for i in range(7)]
    # Same timestamp as Job 3: the id tie-breaker must keep paging stable.
    twin = add_post(db, src, "Job 3 twin", posted_at=posts[3].posted_at)

    seen, cursor = [], None
    while True:
        params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
        body = client.get("/posts", params=params).json()
        seen += [i["id"] for i in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    expected = [p.id for p in posts[:3]] + [twin.id] + [p.id for p in posts[3:]]
    assert seen == expected


def test_search_is_accent_insensitive_and_ranked(db, client) -> None:
    src = add_source(db)
    in_body = add_post(
        db,
        src,
        "Developer",
        body="Chúng tôi cần kỹ sư phần mềm giỏi",
        posted_at=NOW - timedelta(hours=1),
    )
    in_title = add_post(
        db,
        src,
        "Kỹ sư phần mềm Python",
        body="Làm việc tại Hà Nội",
        posted_at=NOW - timedelta(hours=2),
    )
    add_post(db, src, "Tester", body="Manual testing")

    # Title (weight A) outranks body (weight C), whatever the dates.
    assert ids(client.get("/posts", params={"q": "ky su phan mem"})) == [in_title.id, in_body.id]
    assert ids(client.get("/posts", params={"q": "Kỹ Sư"})) == [in_title.id, in_body.id]
    newest = client.get("/posts", params={"q": "ky su", "sort": "newest"})
    assert ids(newest) == [in_body.id, in_title.id]
    assert len(ids(client.get("/posts", params={"q": "   "}))) == 3  # blank q = no search


def test_relevance_cursor(db, client) -> None:
    src = add_source(db)
    for i in range(5):
        add_post(db, src, "python " * (i + 1))
    first = client.get("/posts", params={"q": "python", "limit": 2}).json()
    second = client.get(
        "/posts", params={"q": "python", "limit": 10, "cursor": first["next_cursor"]}
    ).json()
    all_ids = [i["id"] for i in first["items"] + second["items"]]
    assert len(all_ids) == len(set(all_ids)) == 5
    assert second["next_cursor"] is None


def test_attribute_and_column_filters(db, client) -> None:
    itviec = add_source(db)
    fb = add_source(db, "facebook")
    senior_hn = add_post(
        db,
        itviec,
        "Senior Python",
        language="en",
        attributes={"levels": ["senior"], "locations": ["hanoi"], "work_mode": "remote"},
    )
    lead_hcm = add_post(
        db,
        itviec,
        "Lead Java",
        language="en",
        attributes={"levels": ["lead"], "locations": ["hcmc", "hanoi"]},
    )
    junior_fb = add_post(
        db,
        fb,
        "Tuyển junior",
        language="vi",
        attributes={"levels": ["junior"], "locations": ["hcmc"]},
    )

    assert ids(client.get("/posts?levels=senior&levels=lead")) == [senior_hn.id, lead_hcm.id]
    assert ids(client.get("/posts?locations=hcmc")) == [lead_hcm.id, junior_fb.id]
    assert ids(client.get("/posts?levels=lead&locations=hanoi")) == [lead_hcm.id]
    assert ids(client.get("/posts?work_mode=remote")) == [senior_hn.id]
    assert ids(client.get("/posts?language=vi")) == [junior_fb.id]
    assert ids(client.get("/posts?source_type=facebook")) == [junior_fb.id]
    # Unknown parameters are ignored; other categories have their own posts.
    assert len(ids(client.get("/posts?bogus=1"))) == 3
    assert ids(client.get("/posts?category=vehicle")) == []


def test_salary_filter_converts_vnd(db, client) -> None:
    src = add_source(db)
    usd = add_post(db, src, "USD", price_min=Decimal(1500), price_max=Decimal(2500), currency="USD")
    vnd = add_post(
        db, src, "VND", price_min=Decimal(40_000_000), price_max=Decimal(60_000_000), currency="VND"
    )
    add_post(db, src, "Low", price_max=Decimal(15_000_000), currency="VND")
    add_post(db, src, "Negotiable", attributes={"salary_negotiable": True})

    # 60M VND / 26,000 ≈ 2,308 USD
    assert ids(client.get("/posts", params={"salary_min_usd": 2000})) == [usd.id, vnd.id]
    assert ids(client.get("/posts", params={"salary_min_usd": 2400})) == [usd.id]


def test_since_filter(db, client) -> None:
    src = add_source(db)
    recent = add_post(db, src, "Recent", posted_at=NOW - timedelta(hours=2))
    add_post(db, src, "Old", posted_at=NOW - timedelta(days=3))
    assert ids(client.get("/posts", params={"since": "24h"})) == [recent.id]
    assert len(ids(client.get("/posts", params={"since": "7d"}))) == 2
    assert client.get("/posts", params={"since": "1y"}).status_code == 422


def test_hidden_duplicates_and_also_on(db, client) -> None:
    itviec = add_source(db)
    linkedin = add_source(db, "linkedin")
    canonical = add_post(db, itviec, "Backend Engineer", posted_at=NOW - timedelta(hours=5))
    dup = add_post(db, linkedin, "Backend Engineer", duplicate_of_id=canonical.id, posted_at=NOW)
    hidden = add_post(db, itviec, "Hidden", is_hidden=True)

    items = client.get("/posts").json()["items"]
    assert [i["id"] for i in items] == [canonical.id]
    assert items[0]["also_on"] == ["linkedin"]

    with_dups = client.get("/posts", params={"include_duplicates": True}).json()["items"]
    assert {i["id"]: i["also_on"] for i in with_dups} == {
        dup.id: ["itviec"],
        canonical.id: ["linkedin"],
    }
    assert hidden.id in ids(client.get("/posts", params={"include_hidden": True}))


def test_stale_posts_are_hidden_except_when_saved(db, client) -> None:
    itviec = add_source(db)
    fb = add_source(db, "facebook")
    fresh = add_post(db, itviec, "Fresh")
    stale = add_post(db, itviec, "Not seen for 40 days", last_seen_at=NOW - timedelta(days=40))
    old_fb = add_post(db, fb, "Old group post", posted_at=NOW - timedelta(days=40))
    saved_stale = add_post(
        db,
        itviec,
        "Saved",
        is_saved=True,
        last_seen_at=NOW - timedelta(days=40),
        posted_at=NOW - timedelta(days=41),
    )

    assert ids(client.get("/posts")) == [fresh.id]
    assert set(ids(client.get("/posts", params={"include_stale": True}))) == {
        fresh.id,
        stale.id,
        old_fb.id,
        saved_stale.id,
    }
    assert ids(client.get("/posts", params={"saved": True})) == [saved_stale.id]


@pytest.mark.parametrize(
    "cursor",
    ["not-base64!", encode_cursor("relevance", 0.5, 1)],
    ids=["malformed", "other-sort"],
)
def test_invalid_cursor_is_400(client, cursor) -> None:
    response = client.get("/posts", params={"cursor": cursor})
    assert response.status_code == 400
    assert "Invalid cursor" in response.json()["detail"]


def test_limit_bounds(client) -> None:
    assert client.get("/posts", params={"limit": 51}).status_code == 422
    assert client.get("/posts", params={"limit": 0}).status_code == 422


# --- /posts/{id} ---------------------------------------------------------------------------


def test_post_detail_lists_duplicates(db, client) -> None:
    itviec = add_source(db)
    linkedin = add_source(db, "linkedin")
    canonical = add_post(db, itviec, "Data Engineer", body="Full description")
    dup = add_post(db, linkedin, "Data Engineer", duplicate_of_id=canonical.id)

    detail = client.get(f"/posts/{canonical.id}").json()
    assert detail["body"] == "Full description"
    assert [d["id"] for d in detail["duplicates"]] == [dup.id]
    assert detail["duplicates"][0]["source"]["type"] == "linkedin"

    from_dup = client.get(f"/posts/{dup.id}").json()
    assert [d["id"] for d in from_dup["duplicates"]] == [canonical.id]
    assert from_dup["also_on"] == ["itviec"]
    assert client.get("/posts/999999").status_code == 404


def test_patch_post_saves_and_hides(db, client) -> None:
    post = add_post(db, add_source(db), "Job")
    response = client.patch(f"/posts/{post.id}", json={"is_saved": True})
    assert response.status_code == 200
    assert response.json()["is_saved"] is True
    assert response.json()["is_hidden"] is False

    client.patch(f"/posts/{post.id}", json={"is_hidden": True})
    assert ids(client.get("/posts")) == []
    assert client.patch(f"/posts/{post.id}", json={"title": "x"}).status_code == 422
    assert client.patch("/posts/999999", json={"is_saved": True}).status_code == 404


# --- /sources and /runs --------------------------------------------------------------------


def test_list_sources_with_last_run_and_counts(db, client) -> None:
    itviec = add_source(db, config={"pages": 3})
    add_source(db, "linkedin", enabled=False)
    add_post(db, itviec, "Job")
    db.add(CrawlRun(source_id=itviec.id, status=CrawlRunStatus.SUCCESS))
    db.commit()
    db.add(CrawlRun(source_id=itviec.id, status=CrawlRunStatus.FAILED, error="boom"))
    db.commit()

    body = client.get("/sources").json()
    assert [s["type"] for s in body] == ["itviec", "linkedin"]
    first, second = body
    assert first["post_count"] == 1
    assert first["last_run"]["status"] == "failed"
    assert first["plugin_available"] is True
    assert first["category"] == "job"
    assert first["config"] == {"pages": 3}
    assert second["last_run"] is None
    assert second["plugin_available"] is False  # no LinkedIn plugin yet
    assert client.get(f"/sources/{itviec.id}").json()["id"] == itviec.id
    assert client.get("/sources/9999").status_code == 404


def test_patch_source(db, client) -> None:
    src = add_source(db)
    response = client.patch(
        f"/sources/{src.id}",
        json={"enabled": False, "interval_minutes": 180, "config": {"pages": 1}},
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["enabled"], body["interval_minutes"], body["config"]) == (False, 180, {"pages": 1})

    too_low = client.patch(f"/sources/{src.id}", json={"interval_minutes": 5})
    assert too_low.status_code == 422
    assert "at least 30" in too_low.json()["detail"]
    assert client.patch(f"/sources/{src.id}", json={"enabled": None}).status_code == 422
    assert client.patch(f"/sources/{src.id}", json={"status": "ok"}).status_code == 422


def test_run_now_queues_once(db, client) -> None:
    src = add_source(db)
    response = client.post(f"/sources/{src.id}/run")
    assert response.status_code == 202
    run_id = response.json()["run_id"]

    run = client.get(f"/runs/{run_id}").json()
    assert (run["status"], run["trigger"]) == ("queued", "manual")

    again = client.post(f"/sources/{src.id}/run")
    assert again.status_code == 409
    assert str(run_id) in again.json()["detail"]


def test_run_now_refuses_needs_login_and_missing_plugin(db, client) -> None:
    blocked = add_source(db, status=SourceStatus.NEEDS_LOGIN)
    assert client.post(f"/sources/{blocked.id}/run").status_code == 409
    no_plugin = add_source(db, "linkedin")
    response = client.post(f"/sources/{no_plugin.id}/run")
    assert response.status_code == 409
    assert "plugin" in response.json()["detail"]
    assert client.post("/sources/9999/run").status_code == 404


def test_list_runs(db, client) -> None:
    a, b = add_source(db), add_source(db, "facebook")
    for source, status in [
        (a, CrawlRunStatus.SUCCESS),
        (b, CrawlRunStatus.FAILED),
        (a, CrawlRunStatus.QUEUED),
    ]:
        finished = NOW if status == CrawlRunStatus.SUCCESS else None
        db.add(CrawlRun(source_id=source.id, status=status, finished_at=finished))
        db.commit()

    runs = client.get("/runs").json()
    assert [r["status"] for r in runs] == ["queued", "failed", "success"]
    assert runs[2]["duration_seconds"] is not None
    assert len(client.get("/runs", params={"source_id": a.id}).json()) == 2
    assert len(client.get("/runs", params={"status": "failed"}).json()) == 1
    assert len(client.get("/runs", params={"limit": 1}).json()) == 1
    assert client.get("/runs", params={"status": "nope"}).status_code == 422
    assert client.get("/runs/9999").status_code == 404


# --- /stats, /meta, /health ----------------------------------------------------------------


def test_stats(db, client) -> None:
    itviec = add_source(db)
    fb = add_source(db, "facebook")
    add_post(db, itviec, "New", is_saved=True)
    add_post(db, itviec, "Old crawl", crawled_at=NOW - timedelta(days=2))
    add_post(db, fb, "Hidden", is_hidden=True)

    body = client.get("/stats").json()
    assert (body["total"], body["new_24h"], body["saved"]) == (2, 1, 1)
    assert body["by_category"] == [{"category": "job", "count": 2}]
    per_source = {s["source"]["type"]: (s["count"], s["new_24h"]) for s in body["by_source"]}
    assert per_source == {"itviec": (2, 1), "facebook": (0, 0)}


def test_meta_categories(db, client) -> None:
    src = add_source(db)
    add_post(
        db,
        src,
        "Senior",
        language="en",
        attributes={"levels": ["senior"], "skills": ["python", "sql"], "locations": ["hcmc"]},
    )
    add_post(
        db,
        src,
        "Senior 2",
        language="vi",
        attributes={"levels": ["senior", "lead"], "skills": ["python"]},
    )
    add_post(db, src, "Car", category="vehicle")

    cats = {c["key"]: c for c in client.get("/meta/categories").json()["categories"]}
    assert list(cats) == ["job", "vehicle"]
    job = cats["job"]
    assert (job["label"], job["price_label"], job["post_count"]) == ("Jobs", "Salary", 2)

    filters = {f["key"]: f for f in job["filters"]}
    assert list(filters)[:2] == ["source_type", "language"]
    assert filters["source_type"]["options"] == [{"value": "itviec", "label": "ITviec", "count": 2}]
    assert {o["value"]: o["count"] for o in filters["language"]["options"]} == {"vi": 1, "en": 1}
    levels = {o["value"]: o["count"] for o in filters["levels"]["options"]}
    assert list(levels) == ["intern", "fresher", "junior", "mid", "senior", "lead", "manager"]
    assert (levels["senior"], levels["lead"], levels["intern"]) == (2, 1, 0)
    assert [(o["value"], o["count"]) for o in filters["skills"]["options"]] == [
        ("python", 2),
        ("sql", 1),
    ]
    assert filters["locations"]["options"] == [
        {"value": "hcmc", "label": "Ho Chi Minh City", "count": 1}
    ]
    assert [f["key"] for f in cats["vehicle"]["filters"]] == ["source_type", "language"]


@pytest.mark.parametrize(
    ("age", "code", "status"),
    [(None, 503, "missing"), (10, 200, "ok"), (600, 503, "stale")],
)
def test_worker_health(db, client, age, code, status) -> None:
    if age is not None:
        db.add(WorkerHeartbeat(worker_id="w1", beat_at=NOW - timedelta(seconds=age)))
        db.commit()
    response = client.get("/health/worker")
    assert response.status_code == code
    assert response.json()["status"] == status
