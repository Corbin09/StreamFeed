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

# Backend (run from backend/)
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload
.venv/bin/pytest                                  # all tests
.venv/bin/pytest tests/test_health.py::test_health  # single test
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Postgres is mapped to host port **5433**, because 5432 is taken on the dev machine. Inside compose, the backend reaches it at `postgres:5432`.

## Architecture

### Built so far

- `docker-compose.yml`: Postgres 16 + FastAPI backend.
- `backend/app/main.py`: FastAPI app with a `/health` endpoint.
- `backend/app/config.py`: pydantic-settings that read `.env` or `../.env`. `CORS_ORIGINS` is a comma-separated string, exposed as `cors_origin_list`.
- `data/seeds/sources.yaml`: source definitions.

### Planned design (follow it when adding features)

Data flow:

```
crawlers → normalize/dedup/classify pipeline → Postgres → FastAPI → Next.js
```

- **Generic `posts` table** for every category. It has a `category` column (`job`, `vehicle`, ...) and an `attributes` JSONB column for category-specific fields, validated by per-category Pydantic schemas in `app/schemas/attributes/`. A new category should need only a new schema, not a table migration. The other tables are `sources` (per-source config JSONB, status, interval) and `crawl_runs`.
- **Crawlers are plugins** in `app/crawlers/`:
  - Each one subclasses `BaseCrawler` and implements `fetch()` → raw and `parse(raw)` → `RawPost`.
  - Crawlers are registered in `registry.py`.
  - Each fetch saves its raw HTML/JSON to `data/raw/<source>/<date>/`, so parsers can be re-run without re-crawling.
- **Tools per source:**
  - ITviec uses httpx + selectolax.
  - LinkedIn and Facebook use Playwright persistent profiles under `data/browser_profiles/` (gitignored, never commit). These profiles are created by a manual headed login CLI.
  - On a login or checkpoint page, the crawler must mark the source `needs_login` and stop. Never retry in a loop.
  - Keep rate limits low, with random delays.
- **Dedup** runs in two layers: `source + external_id` first, then `content_hash` (normalized title + company) to merge the same post across sources.
- **Search:**
  - Uses Postgres full-text with the `simple` config + `unaccent` + `pg_trgm`. Do not use language-specific stemming.
  - Each post stores a `language` field (`vi`/`en`), detected with lingua.
  - Locations and levels are stored as canonical English keys (e.g. `hanoi`).
- **Scheduler:** APScheduler worker, default 120-minute interval per source, with jitter.
- **Frontend:** Next.js + Tailwind + React Query. Category tabs switch the feed; the filter sidebar is generated from the active category's attribute schema; `/admin/sources` shows crawl status.
