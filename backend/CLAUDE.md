# backend/CLAUDE.md

Guidance for work inside `backend/` (FastAPI API, worker, crawlers, pipeline). The root
`CLAUDE.md` and `docs/OPERATIONS.md` still apply. Run commands from `backend/`; set up the
host environment once with `../scripts/run.sh dev-setup`.

## Layout

```
app/main.py            FastAPI app; routers in app/api/routes/
app/config.py          settings (reads .env or ../.env)
app/models/            SQLAlchemy models; statuses are StrEnums enforced by CHECK constraints
app/schemas/attributes per-category attribute models (job.py); fields with
                       json_schema_extra={"filter": "<Label>"} become GET /posts filters
app/crawlers/          base.py, fetchers.py (Http/Browser), raw_store.py, registry.py,
                       runner.py, login.py, one module per source plugin
app/pipeline/          ingest.py (entry), classify.py + llm.py (hiring filter),
                       language.py, salary.py, normalize.py, dedup.py,
                       data/*.yaml alias dictionaries (levels, locations, skills)
app/services/search.py feed query, visibility rules, keyset cursors
app/worker/            worker loop, heartbeat, housekeeping
app/scheduling.py      daily_window parsing and next-run calculation
alembic/versions/      migrations (date-prefixed file names)
tests/                 pytest; fixtures/<source>/ hold parser fixtures
```

## Recipes

**Add a crawler plugin (new source):**
1. `app/crawlers/<name>.py`: subclass `BaseCrawler`; set `type`, `label`, `category`,
   `min_interval_minutes` (and `filter_non_jobs = True` for mixed content such as groups);
   implement `fetch` (yields `RawPage`, network allowed) and `parse` (pure: `RawPage` →
   `list[RawPost]`, no network, no DB); override `create_fetcher` for a browser source.
2. Add the module to `BUILTIN_MODULES` in `app/crawlers/registry.py`.
3. Add an entry to `../data/seeds/sources.yaml` (`enabled: false` until it works).
4. Add fixtures in `tests/fixtures/<name>/` and tests in `tests/test_<name>.py`.
   Logged-in sites: synthetic fixtures only (no real people's data).
5. Login-based site: add it to `SITES` in `app/crawlers/login.py`, raise `NeedsLogin` on
   any login/checkpoint/block page, and allow it in `scripts/run.sh login`.
6. Update `docs/ARCHITECTURE.md`, the root `CLAUDE.md` bullet list and, if the picture
   changes, `scripts/render-overview.py`.

**Add a category (phase 2):** new `app/schemas/attributes/<category>.py`, value in
`Category` (`app/models/post.py`, already has vehicle/real_estate/keyboard/mouse), alias
dictionaries if needed. No migration and no frontend change: filters come from
`/meta/categories`.

**Add a status value:** update the `StrEnum` **and** write a migration that rebuilds the
CHECK constraint.

**Schema change:** edit models → `.venv/bin/alembic revision --autogenerate -m "..."` →
review the generated file → `.venv/bin/alembic upgrade head`. Indexes, generated columns
and queries that strip accents must use `f_unaccent(...)`, never `unaccent(...)`.

**Fix a normalization mapping:** edit `app/pipeline/data/*.yaml`, then
`python -m app.crawlers reparse <run_id>` (inside the worker:
`docker compose exec worker python -m app.crawlers reparse <run_id>`).

## Tests

- `.venv/bin/pytest` (or `../scripts/run.sh check` for everything). DB tests need Postgres
  on 127.0.0.1:5433 and use a separate `streamfeed_test` database; they are skipped when it
  is down.
- Fixtures: `session` (rolled-back transaction) for plain DB code; `session_factory` (real
  commits, tables truncated afterwards) for code that opens its own sessions (runner,
  worker, API via `get_session` override).
- Fake crawlers are registered on a local `CrawlerRegistry` (see `tests/test_runner.py`).
- The LLM is mocked with `httpx.MockTransport` (`tests/test_llm.py`); tests never call Ollama.
- In-page JavaScript (Facebook/LinkedIn) is checked with `node --check`.
- Style: ruff, line length 100, target py312. `ruff check . && ruff format --check .`
