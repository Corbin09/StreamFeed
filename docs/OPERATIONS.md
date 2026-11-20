# Operations notes

Read this before running, deploying or rebuilding StreamFeed on a new machine. It is imported
by the root `CLAUDE.md`, so Claude Code loads it in every session. Keep it short and true:
update it when a command, a default or a gotcha changes.

## 1. Fresh clone → running app

Prerequisites: Docker Engine with the Compose v2 plugin (user in the `docker` group), ~6 GB
free disk (images + the 1.9 GB LLM), ~4 GB free RAM. For `login`, `check` and `dev-setup`
also Python 3.12+ and Node.js 20+ on the host.

```bash
git clone <repo> StreamFeed && cd StreamFeed
scripts/run.sh up                      # creates .env, builds, starts, waits for the API, prints URLs
scripts/run.sh login facebook          # optional: headed browser, log in with the SECONDARY account
scripts/run.sh login linkedin          # optional: same for LinkedIn
scripts/run.sh restore data/exports/<file>.dump   # optional: bring back an old database
```

`up` is safe to re-run at any time. The backend container applies migrations and upserts
`data/seeds/sources.yaml` on every start. Ollama pulls the model on first start in the
background; until it is ready, the Facebook hiring filter falls back to contact rules.

On a headless server, run `login` on a desktop machine and copy
`data/sessions/<type>.json` to the server (keep mode 600). Then `scripts/run.sh up --prod`.

## 2. What is NOT in git (and how to get it back)

| Item | Where | If lost |
|---|---|---|
| Settings | `.env` | `run.sh up` recreates it from `.env.example`; re-add any custom values (prod passwords). |
| Database (posts, saved/hidden flags, runs, source settings changed in the UI) | Docker volume `streamfeed_pgdata` | Only a dump brings it back: `run.sh backup` → `data/exports/*.dump`. **Copy dumps off the machine.** Without one, crawls rebuild posts, but saved/hidden flags and UI source edits are gone. |
| Login sessions | `data/sessions/*.json` | `run.sh login facebook\|linkedin`. They grant account access; never commit or share them. |
| Raw pages | `data/raw/` | Not needed; only used for `reparse`. Pruned after 30 days anyway. |
| LLM model | Docker volume `streamfeed_ollama` | Re-downloaded automatically on start. |
| Claude Code memory | `~/.claude/projects/...` | Its durable content is in `CLAUDE.md` and this file. |

Source configuration (search keywords, Facebook groups, daily windows) lives in
`data/seeds/sources.yaml`, which is in git. The seed never overwrites `enabled`/`status`
of existing sources, but it does update `config` and `interval_minutes`, so edit the YAML
rather than the UI for changes you want to keep.

## 3. Rules for the crawlers (do not break these)

- Facebook and LinkedIn use a **secondary account** only. Never the user's main account.
- On a login, checkpoint, captcha, authwall or HTTP 429/999 page: stop, mark the source
  `needs_login`, **never retry or work around it**. Fix = `run.sh login <type>`.
- LinkedIn guest (logged-out) access answers 429 to every automated client. It was tried
  and removed (commit `e2ca497`); do not bring it back.
- Keep rates low: Facebook and LinkedIn run once a day inside `daily_window`, with
  3–8 s pauses; ITviec every 120 min with 2–4 s between requests.
- Facebook/LinkedIn test fixtures are synthetic on purpose. Never commit real pages from
  those sites (they contain other people's personal data). ITviec fixtures are trimmed real
  public pages.
- The user's job search is **Hanoi-focused**: Hanoi-skewed results are expected; do not
  widen searches to other cities unless asked.

## 4. Gotchas

- **Ports.** Postgres is on host port **5433** (5432 was taken on the original machine);
  API 8000, Ollama 11434 and Postgres bind to 127.0.0.1 only. Frontend 3000 binds to all
  interfaces in dev; use `--prod` (Basic Auth) before exposing it.
- **Root-owned files.** Containers run as root, so `data/raw/<type>/` and session files
  refreshed by the worker become owned by root. Deleting them on the host needs `sudo`.
  The login CLI still works because it replaces the file atomically in a directory you own.
- **Sessions expire.** When a source shows `needs_login` in `/admin/sources`, log in again.
  The login CLI resets the source status; `run.sh login` also enables it.
- **Prod.** `--prod` publishes only :3000 with Basic Auth; always put HTTPS in front
  (Cloudflare Tunnel, Caddy, ngrok). Set a strong `POSTGRES_PASSWORD` **before the first
  start**: changing it later does not change the password stored in the existing volume.
- **LLM.** Model `hf.co/QuantFactory/Arcee-VyLinh-GGUF:Q4_K_M` (~1.9 GB disk, ~2.5 GB RAM
  while loaded, unloaded after 10 min idle). CPU only. One LLM failure switches the rest
  of the run to contact rules.
- **Time.** Daily windows use `TIMEZONE` (default `Asia/Ho_Chi_Minh`); DB timestamps are UTC.
- **Browser.** The worker image ships Google Chrome stable (`BROWSER_CHANNEL=chrome`). On the
  host, `login` uses `--browser-path`, `BROWSER_EXECUTABLE_PATH`, or Playwright's Chromium
  (installed automatically). The original machine used `--browser-path /usr/bin/brave-browser`.
- **Diagrams.** `scripts/render-overview.py` needs a headless Chrome for the PNG (run
  `scripts/render-diagrams.sh` once to download one). Mermaid exports are not committed.
- `docs/diagrams/template.jpeg` is a third-party style reference used for the overview
  drawing; it is gitignored and not needed.

## 5. Troubleshooting

| Symptom | Check / fix |
|---|---|
| `up` times out waiting for the backend | `run.sh logs backend` (usually a migration error or Postgres password mismatch with an old volume). |
| Source stuck in `needs_login` | `run.sh login <type>`. |
| Source in `error` (5 failures) | `run.sh logs worker`; a selector drift means the site changed markup: save a fresh page as a fixture, fix the parser, then `reparse <run_id>`. |
| Wrong location/level/skill mapping | Fix `backend/app/pipeline/data/*.yaml`, then `docker compose exec worker python -m app.crawlers reparse <run_id>`. |
| Facebook keeps non-job posts | Check the model is ready (`run.sh status`); tune the few-shot prompt in `backend/app/pipeline/llm.py`. |
| DB tests skipped | Postgres is not up: `docker compose up -d postgres` (or `run.sh check`, which starts it). |

## 6. Routine

- Before risky changes or moving machines: `scripts/run.sh backup` and copy the dump elsewhere.
- Before committing: `scripts/run.sh check` (ruff, pytest, eslint, tsc).
- After changing components or their connections: update `docs/ARCHITECTURE.md` and
  `scripts/render-overview.py`, then `scripts/run.sh diagrams`.
