# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

StreamFeed is a personal-use aggregator: it crawls posts from multiple sources into one searchable, filterable, de-duplicated feed. Phase 1 is IT job posts (ITviec, LinkedIn, Facebook groups); phase 2 adds marketplace listings (vehicles, real estate, keyboards, mice). There are no user accounts.

## Language convention

- Everything in the repo is **English**: UI labels, code, identifiers, comments, docs, API, commit messages, logs.
- **Scraped post content may be Vietnamese or English** and is stored unchanged. Search and normalization must handle both languages and both accented and unaccented Vietnamese.
- Talk to the user in Vietnamese in chat.

## Commands

Run all commands from the repo root unless noted.

```bash
cp .env.example .env                 # first time only
docker compose up -d --build         # postgres (host port 5433) + backend (port 8000)
docker compose up -d postgres        # DB only, for local backend dev
curl localhost:8000/health
scripts/render-diagrams.sh           # export Mermaid diagrams to docs/diagrams/mermaid/ (gitignored, on demand)
scripts/render-overview.py           # redraw the illustrated overview (docs/diagrams/streamfeed-overview.*)

# Backend (run from backend/)
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload
.venv/bin/pytest                                  # all tests
.venv/bin/pytest tests/test_health.py::test_health  # single test
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/alembic upgrade head                                 # apply migrations
.venv/bin/alembic revision --autogenerate -m "describe change"  # new migration
.venv/bin/python -m app.seed                                   # upsert data/seeds/sources.yaml
.venv/bin/python -m app.crawlers list|run <source>|reparse <run_id>  # crawler CLI
```

DB tests (`tests/test_db.py`) create and migrate a separate `streamfeed_test` database on the same server. Each test runs in a rolled-back transaction, and the tests are skipped if Postgres is down. The backend container runs `alembic upgrade head` and `app.seed` on every start.

Postgres is mapped to host port **5433**, because 5432 is taken on the dev machine. Inside compose, the backend reaches it at `postgres:5432`.

## Architecture

### Built so far

- `docker-compose.yml`: Postgres 16 + FastAPI backend.
- `backend/app/main.py`: FastAPI app; routers in `app/api/routes/` (see the REST API bullet below).
- `backend/app/config.py`: pydantic-settings that read `.env` or `../.env`. `CORS_ORIGINS` is a comma-separated string, exposed as `cors_origin_list`.
- `data/seeds/sources.yaml`: source definitions, loaded by `app/seed.py`. The loader upserts by `name` but never overwrites the runtime `enabled`/`status` of existing sources.
- `backend/app/models/`: `Source`, `Post`, `CrawlRun` (SQLAlchemy 2.0).
  - Statuses are plain strings validated by CHECK constraints built from `StrEnum`s, so adding a status means a migration that updates the constraint.
  - `sources.type` and `posts.category` are unconstrained strings, so new plugins and categories need no migration.
- **Search plumbing:** the first migration creates the `unaccent` and `pg_trgm` extensions plus `f_unaccent(text)`, an IMMUTABLE wrapper. Always use `f_unaccent` (never `unaccent`) in indexes, generated columns and queries.
  - `posts.search_vector` is a generated column (title A, company B, body C, `simple` config), backed by a GIN index.
  - There is also a trigram GIN index on `f_unaccent(title)`.
  - Query pattern: `search_vector @@ plainto_tsquery('simple', f_unaccent(:q))`.
- **Crawler framework** (`backend/app/crawlers/`):
  - `base.py` holds `BaseCrawler`, `RawPage`, `RawPost`, `CrawlContext` and the `NeedsLogin`/`FetchError` exceptions.
  - `fetchers.py` holds `HttpFetcher`: a random 2–4 s delay between requests, and retries on 429/5xx/transport errors with backoff.
  - `raw_store.py` writes `data/raw/<type>/<date>/<run_id>/NNNN.html|json` plus a manifest. `crawl_runs.raw_dir` stores this path relative to `DATA_DIR`.
  - `registry.py` maps `sources.type` to the plugin class. New built-in plugin modules must be added to `BUILTIN_MODULES`.
  - `runner.py` (`CrawlRunner`) runs one crawl:
    - It takes an advisory lock on `(7301, source_id)` and creates or claims the `crawl_runs` row.
    - It saves every page to the raw store before parsing it. A failing page counts as a parse error and is skipped.
    - It sets the outcome (success / failed / needs_login / selector drift) and updates `consecutive_failures` (status `error` after 5).
- **Pipeline** (`backend/app/pipeline/`): `ingest.process_posts` runs these steps (see ARCHITECTURE.md §7):
  1. `merge_raw_posts`: the first non-empty value wins, so crawlers must yield their most precise page first.
  2. Filter out non-hiring posts when `filter_non_jobs` is set.
  3. `build_row` normalizes the post:
     - `language` (lingua, VI/EN);
     - `salary` (VI/EN amounts and units; values ≥ 100k are VND);
     - `normalize` (locations, levels from the title, skills, work mode).
  4. Validate the attributes against `app/schemas/attributes/<category>.py`.
  5. Upsert on `(source_id, external_id)`, preserving `is_saved`/`is_hidden`.
  6. `dedup.link_duplicates` (same `content_hash`, other source, oldest wins, 30-day window).

  The alias dictionaries are `app/pipeline/data/*.yaml`. Fix a mapping there, then run `reparse <run_id>` to re-apply it without crawling.
- **REST API** (`app/api/`, ARCHITECTURE.md §8): `posts`, `sources`, `runs`, `stats`, `meta`, `health` routers; models in `app/api/schemas.py`, ORM→model mapping in `app/api/serializers.py`.
  - The feed query lives in `app/services/search.py`: filters, default visibility (not hidden, not duplicate, not stale) and keyset cursors over `(sort_key, id)`. `/stats` and `/meta` reuse `visibility_conditions`.
  - **Attribute filters are schema-driven:** a field in `app/schemas/attributes/<category>.py` with `json_schema_extra={"filter": "<Label>"}` becomes a `GET /posts` query parameter of the same name and appears in `/meta/categories`. No router change is needed.
  - "Run now" (`POST /sources/{id}/run`) only inserts a `queued` run (row-locked, 409 on an active run, `needs_login` or missing plugin).
  - Settings: `USD_VND_RATE` (salary filter), `STALE_AFTER_DAYS`. Crawler class attributes `label` and `min_interval_minutes` are exposed to the API.
  - API tests (`tests/test_api.py`) override `get_session` with `session_factory`.
- **ITviec plugin** (`app/crawlers/itviec.py`): fetches listing cards, then detail pages (JSON-LD `JobPosting`); `external_id` is the job slug. Parser tests use trimmed real pages in `tests/fixtures/itviec/`. When ITviec changes its markup, save a fresh page there and update the fixtures.
- **Test helpers:** use the `session_factory` fixture (real commits, tables truncated afterwards) for code that manages its own sessions. Fake crawlers are registered on a local `CrawlerRegistry` (see `tests/test_runner.py`).
- `posts.price_min/price_max/currency` hold the salary for jobs and the asking price for listings. `duplicate_of_id` links a cross-source duplicate to the canonical post.

### Target design

The full design is in **`docs/ARCHITECTURE.md`**: containers, components, data model, flows, pipeline rules, API, reliability policies and ADRs. Read the relevant section before building a step, and update the doc whenever the design changes. Mermaid diagrams live only in the Markdown; their image exports are not committed. The only committed image is the illustrated overview (`docs/diagrams/streamfeed-overview.*`). When components or their connections change, update `scripts/render-overview.py` and re-run it.

Data flow:

```
worker (scheduler → crawlers → pipeline) → Postgres ← api (FastAPI) ← frontend (Next.js)
```

Rules that are easy to get wrong:
- **The `api` process never crawls.** Crawling runs in a separate `worker` process. "Run now" inserts a `crawl_runs` row with `status='queued'`; the worker claims it with `FOR UPDATE SKIP LOCKED`. Each source has a per-source `pg_try_advisory_lock`, and only one browser runs at a time.
- **`BaseCrawler.parse()` must be pure** (no network, no DB). Every fetched page is saved under `data/raw/` first, so runs can be re-parsed and parsers are tested against HTML fixtures.
- **On a login, checkpoint or captcha page, stop at once and set `needs_login`. Never retry.**
- **Login sessions are Playwright `storage_state` JSON files** in `data/sessions/` (gitignored, mode 0600), not persistent profiles. They are created on the host with a headed login CLI.
- **Normalization matches on unaccented, lower-cased text** and stores canonical English keys (`hanoi`, `senior`). The original currency is kept.
