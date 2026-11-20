#!/usr/bin/env bash
# StreamFeed: one entry point for running, operating and checking the project.
#
#   scripts/run.sh [command] [args] [--prod]
#
# Run the app
#   up                  check prerequisites, create .env, build and start every service,
#                       wait until the API is healthy (default command)
#   down                stop and remove the containers (database and model volumes are kept)
#   restart             rebuild and restart every service
#   status              containers, API health, sources and the LLM model
#   logs [service]      follow logs (backend, worker, frontend, postgres, ollama)
#
# Operate
#   login <facebook|linkedin> [--browser-path PATH]
#                       open a visible browser on this machine, log in with the secondary
#                       account, save data/sessions/<type>.json and enable the source
#   crawl <source>      run one crawl now inside the worker (source id, name or type)
#   backup              dump the database to data/exports/streamfeed-<timestamp>.dump
#   restore <file>      replace the database with a dump made by `backup`
#
# Develop
#   dev-setup           backend venv (backend/.venv) + frontend node_modules on the host
#   check               backend ruff + pytest, frontend lint + type-check (starts Postgres)
#   diagrams            redraw docs/diagrams/streamfeed-overview.{svg,png}
#
# --prod adds docker-compose.prod.yml: only :3000 is published and Basic Auth is required.
# On the first --prod start, missing Basic Auth credentials are generated into .env.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$(pwd)

compose_files=(-f docker-compose.yml)
prod=0
args=()
for arg in "$@"; do
  if [[ "$arg" == "--prod" ]]; then
    compose_files+=(-f docker-compose.prod.yml)
    prod=1
  else
    args+=("$arg")
  fi
done
set -- "${args[@]+"${args[@]}"}"

cmd="${1:-up}"
shift || true

# ------------------------------------------------------------------ helpers
info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarn:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; }

dc() { docker compose "${compose_files[@]}" "$@"; }

env_value() { # env_value KEY -> value from .env (empty if unset)
  grep -E "^$1=" .env 2>/dev/null | tail -n 1 | cut -d= -f2- || true
}

set_env_value() { # set_env_value KEY VALUE (replace or append in .env)
  if grep -qE "^$1=" .env; then
    sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak
  else
    printf '%s=%s\n' "$1" "$2" >>.env
  fi
}

require_docker() {
  command -v docker >/dev/null || die "Docker is not installed (https://docs.docker.com/engine/install/)."
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 plugin is missing ('docker compose')."
  docker info >/dev/null 2>&1 || die "Cannot talk to the Docker daemon (is it running? is your user in the 'docker' group?)."
}

ensure_env() {
  if [[ ! -f .env ]]; then
    cp .env.example .env
    info "Created .env from .env.example"
  fi
  if [[ $prod -eq 1 ]]; then
    if [[ -z "$(env_value BASIC_AUTH_USER)" ]]; then
      set_env_value BASIC_AUTH_USER streamfeed
    fi
    if [[ -z "$(env_value BASIC_AUTH_PASSWORD)" ]]; then
      set_env_value BASIC_AUTH_PASSWORD "$(head -c 18 /dev/urandom | base64 | tr -d '/+=')"
      info "Generated Basic Auth credentials in .env (BASIC_AUTH_USER / BASIC_AUTH_PASSWORD)"
    fi
  fi
}

ensure_data_dirs() {
  mkdir -p data/raw data/sessions data/exports
  chmod 700 data/sessions 2>/dev/null || true
}

check_ports() {
  local ports=(3000)
  [[ $prod -eq 0 ]] && ports+=("$(env_value POSTGRES_PORT || true)" 8000 11434)
  for p in "${ports[@]}"; do
    [[ -z "$p" ]] && continue
    if command -v ss >/dev/null && ss -ltnH "sport = :$p" 2>/dev/null | grep -q .; then
      # Our own containers may already hold the port; only warn when they are not running.
      if [[ -z "$(dc ps -q 2>/dev/null)" ]]; then
        warn "Port $p is already in use by another program; the stack may fail to start."
      fi
    fi
  done
}

wait_for_api() {
  echo -n "Waiting for the backend to become healthy"
  for _ in $(seq 1 90); do
    local status
    status=$(docker inspect -f '{{.State.Health.Status}}' "$(dc ps -q backend)" 2>/dev/null || true)
    if [[ "$status" == "healthy" ]]; then
      echo " ok"
      return 0
    fi
    echo -n "."
    sleep 2
  done
  echo
  die "The backend did not become healthy; check: scripts/run.sh logs backend"
}

api() { # api METHOD PATH [JSON] -> response body; runs inside the backend container (works with --prod)
  dc exec -T backend python -c '
import json, sys, urllib.request
method, path = sys.argv[1], sys.argv[2]
data = sys.argv[3].encode() if len(sys.argv) > 3 else None
req = urllib.request.Request("http://localhost:8000" + path, data=data, method=method,
                             headers={"Content-Type": "application/json"})
print(urllib.request.urlopen(req, timeout=10).read().decode())
' "$@"
}

print_sources() {
  api GET /sources 2>/dev/null | python3 -c '
import json, sys
for s in json.load(sys.stdin):
    flag = "on " if s["enabled"] else "off"
    print("  [{}] #{id} {type:<9} {status:<12} posts={pc:<5} {name}".format(flag, pc=s.get("post_count") or 0, **s))
' || warn "Could not list sources (API not reachable)."
}

model_status() {
  local model
  model=$(env_value LLM_MODEL)
  model=${model:-hf.co/QuantFactory/Arcee-VyLinh-GGUF:Q4_K_M}
  if dc exec -T ollama ollama show "$model" >/dev/null 2>&1; then
    echo "  LLM model ready: $model"
  else
    echo "  LLM model not downloaded yet (~1.9 GB, pulled in the background): $model"
    echo "  Until it is ready the Facebook hiring filter uses the contact rules."
  fi
}

print_urls() {
  echo
  echo "Frontend: http://localhost:3000"
  if [[ $prod -eq 1 ]]; then
    echo "          Basic Auth user: $(env_value BASIC_AUTH_USER)  (password in .env)"
    echo "          API, Postgres and Ollama are not published; put HTTPS in front."
  else
    echo "API:      http://localhost:8000  (docs: http://localhost:8000/docs)"
    echo "Postgres: 127.0.0.1:$(env_value POSTGRES_PORT || echo 5433)"
  fi
  echo
  echo "Sources:"
  print_sources
  model_status
  echo
  echo "Facebook/LinkedIn need a login first: scripts/run.sh login facebook|linkedin"
  echo "Logs: scripts/run.sh logs [service]"
}

ensure_venv() {
  if [[ ! -x backend/.venv/bin/python ]]; then
    info "Creating backend/.venv"
    command -v python3 >/dev/null || die "python3 (3.12+) is required."
    python3 -m venv backend/.venv
  fi
  if ! backend/.venv/bin/python -c 'import app, pytest' >/dev/null 2>&1; then
    info "Installing backend dependencies"
    backend/.venv/bin/pip install -q --upgrade pip
    (cd backend && .venv/bin/pip install -q -e ".[dev]")
  fi
}

ensure_node_modules() {
  command -v npm >/dev/null || die "Node.js 20+ and npm are required for the frontend checks."
  if [[ ! -d frontend/node_modules ]]; then
    info "Installing frontend dependencies"
    (cd frontend && npm ci)
  fi
}

ensure_postgres() {
  ensure_env
  if [[ $prod -eq 1 ]]; then
    die "This command needs the Postgres port on the host; run it without --prod."
  fi
  dc up -d --wait postgres >/dev/null
}

# ------------------------------------------------------------------ commands
case "$cmd" in
  up)
    require_docker
    ensure_env
    ensure_data_dirs
    check_ports
    info "Building and starting the stack ($([[ $prod -eq 1 ]] && echo prod || echo dev))"
    dc up -d --build
    wait_for_api
    print_urls
    ;;

  down)
    require_docker
    dc down
    ;;

  restart)
    require_docker
    ensure_env
    ensure_data_dirs
    dc down
    dc up -d --build
    wait_for_api
    print_urls
    ;;

  status)
    require_docker
    dc ps
    echo
    if api GET /health >/dev/null 2>&1; then
      echo "API: healthy"
      echo "Sources:"
      print_sources
      model_status
    else
      echo "API: not reachable"
    fi
    ;;

  logs)
    dc logs -f --tail=100 "$@"
    ;;

  login)
    site="${1:-}"
    [[ "$site" == "facebook" || "$site" == "linkedin" ]] || die "Usage: scripts/run.sh login facebook|linkedin [--browser-path PATH]"
    shift
    require_docker
    [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] ||
      warn "No graphical display found. Log in on a desktop machine and copy data/sessions/$site.json here (mode 600)."
    ensure_postgres
    ensure_venv
    ensure_data_dirs
    # Playwright's own Chromium is the fallback when no --browser-path / BROWSER_* is set.
    if [[ " $* " != *" --browser-path "* ]] && [[ -z "$(env_value BROWSER_EXECUTABLE_PATH)" ]]; then
      backend/.venv/bin/python -m playwright install chromium >/dev/null
    fi
    (cd backend && .venv/bin/python -m app.crawlers.login "$site" "$@")
    if [[ -n "$(dc ps -q backend)" ]]; then
      id=$(api GET /sources | python3 -c "import json,sys; print(next((s['id'] for s in json.load(sys.stdin) if s['type']=='$site'), ''))")
      if [[ -n "$id" ]]; then
        api PATCH "/sources/$id" '{"enabled": true}' >/dev/null
        info "Enabled source #$id ($site). It runs once a day in its daily_window; 'Run now' in /admin/sources runs it immediately."
      fi
    else
      warn "The API is not running; enable the $site source in /admin/sources after 'scripts/run.sh up'."
    fi
    ;;

  crawl)
    [[ -n "${1:-}" ]] || die "Usage: scripts/run.sh crawl <source id|name|type>"
    require_docker
    dc exec worker python -m app.crawlers run "$@"
    ;;

  backup)
    require_docker
    ensure_data_dirs
    file="data/exports/streamfeed-$(date +%Y%m%d-%H%M%S).dump"
    user=$(env_value POSTGRES_USER); db=$(env_value POSTGRES_DB)
    dc exec -T postgres pg_dump -U "${user:-streamfeed}" -d "${db:-streamfeed}" -Fc >"$file"
    info "Saved $file ($(du -h "$file" | cut -f1)). Copy it off this machine; data/exports is not in git."
    ;;

  restore)
    file="${1:-}"
    [[ -f "$file" ]] || die "Usage: scripts/run.sh restore data/exports/<file>.dump"
    require_docker
    ensure_env
    user=$(env_value POSTGRES_USER); db=$(env_value POSTGRES_DB)
    user=${user:-streamfeed}; db=${db:-streamfeed}
    read -r -p "This replaces every row in database '$db'. Continue? [y/N] " ok
    [[ "$ok" == "y" || "$ok" == "Y" ]] || exit 1
    dc stop worker backend >/dev/null 2>&1 || true
    dc up -d --wait postgres >/dev/null
    dc exec -T postgres dropdb -U "$user" --if-exists --force "$db"
    dc exec -T postgres createdb -U "$user" "$db"
    dc exec -T postgres pg_restore -U "$user" -d "$db" --no-owner <"$file"
    info "Restored $file; starting the stack (migrations run on backend start)"
    dc up -d
    wait_for_api
    ;;

  dev-setup)
    ensure_env
    ensure_venv
    ensure_node_modules
    info "Host dev environment ready: backend/.venv and frontend/node_modules"
    ;;

  check)
    require_docker
    ensure_postgres
    ensure_venv
    ensure_node_modules
    info "Backend: ruff"
    (cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .)
    info "Backend: pytest"
    (cd backend && .venv/bin/pytest -q)
    info "Frontend: lint + type-check"
    (cd frontend && npm run -s lint && npx tsc --noEmit)
    info "All checks passed"
    ;;

  diagrams)
    python3 scripts/render-overview.py
    ;;

  -h|--help|help)
    usage
    ;;

  *)
    usage >&2
    die "Unknown command: $cmd"
    ;;
esac
