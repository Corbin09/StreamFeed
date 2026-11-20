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
  sessions/          Playwright login sessions (gitignored, keep private!)
  seeds/             source definitions (sources.yaml)
  exports/           database dumps from scripts/run.sh backup (gitignored)
```

## Getting started

Requires Docker with the Compose v2 plugin. One script does the rest:

```bash
scripts/run.sh up                   # create .env, build, start, wait for the API, print URLs
open http://localhost:3000          # the feed
scripts/run.sh login facebook       # optional: log in with a secondary account (also: linkedin)
scripts/run.sh help                 # every command: status, logs, crawl, backup, restore, check...
```

Before moving to another machine, run `scripts/run.sh backup` and keep the dump: the database
and login sessions are not in git. See [docs/OPERATIONS.md](docs/OPERATIONS.md).

### Local backend development

```bash
scripts/run.sh dev-setup            # backend/.venv + frontend/node_modules
docker compose up -d postgres
cd backend && .venv/bin/uvicorn app.main:app --reload
scripts/run.sh check                # ruff, pytest, eslint, tsc
```

### Deploy or share a demo

Only the frontend is published; it proxies `/api` to the backend and asks for a login.

```bash
# set a strong POSTGRES_PASSWORD in .env before the first start; Basic Auth credentials are generated if empty
scripts/run.sh up --prod
ngrok http 3000          # or Cloudflare Tunnel / Caddy: always serve it over HTTPS
```

The worker crawls ITviec every 2 hours and Facebook/LinkedIn once a day inside their `daily_window` (see `data/seeds/sources.yaml`).

### Local frontend development

```bash
cd frontend
npm install
npm run dev        # http://localhost:3000, expects the API on http://localhost:8000
```

## Architecture diagrams

The diagrams in `docs/ARCHITECTURE.md` are Mermaid blocks. GitHub and VS Code (with a Mermaid preview extension) render them inline. To export them as image files:

```bash
scripts/render-diagrams.sh                                   # docs/ARCHITECTURE.md -> docs/diagrams/mermaid/*.svg|png (gitignored)
scripts/render-diagrams.sh docs/ARCHITECTURE.md out/ "png pdf" # custom input, output dir, formats
```

The illustrated overview at the top of the doc (`docs/diagrams/streamfeed-overview.svg|png`) is drawn by a separate script. Edit its layout section and re-run it:

```bash
scripts/render-overview.py
```

The scripts require Node.js 18+. The first run downloads mermaid-cli and Chromium. Re-run it after editing a diagram.

## Notes on LinkedIn / Facebook crawling

These sources are crawled once a day with a logged-in browser session (`scripts/run.sh login facebook|linkedin`). Use a **secondary account** and never commit `data/sessions/`. When a login or checkpoint page is detected, the source is paused and marked `needs_login`; log in again to resume. Facebook posts are kept only when a local LLM (Ollama) or the contact rules classify them as hiring posts.
