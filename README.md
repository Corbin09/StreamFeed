# StreamFeed

A personal aggregator that collects posts from multiple sources into one searchable, filterable, de-duplicated feed.

- **Phase 1:** IT job posts from ITviec, LinkedIn and Facebook groups.
- **Phase 2:** marketplace listings (vehicles, real estate, keyboards, mice, ...).

Post content may be in Vietnamese or English; it is stored as-is and searchable with or without diacritics.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full system design.

## Stack

| Layer    | Technology                                    |
|----------|-----------------------------------------------|
| Backend  | Python 3.12+, FastAPI, SQLAlchemy, Alembic    |
| Crawlers | httpx + selectolax, Playwright (logged-in)    |
| Database | PostgreSQL 16 (`unaccent`, `pg_trgm`)         |
| Frontend | Next.js, Tailwind CSS, React Query            |

## Project layout

```
backend/    FastAPI app, crawlers, normalization pipeline, scheduler
frontend/   Next.js UI
data/
  raw/               raw crawled HTML/JSON (gitignored)
  browser_profiles/  Playwright login sessions (gitignored, keep private!)
  seeds/             source definitions (sources.yaml)
  exports/           CSV exports (gitignored)
```

## Getting started

### With Docker

```bash
cp .env.example .env
docker compose up --build
curl http://localhost:8000/health   # {"status":"ok"}
```

### Local backend development

```bash
docker compose up -d postgres
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
pytest
```

## Notes on LinkedIn / Facebook crawling

These sources are crawled with a logged-in browser session. Use a **secondary account**, keep crawl rates low, and never commit `data/browser_profiles/`. When a login or checkpoint page is detected, the source is paused and marked `needs_login`.
