#!/usr/bin/env bash
# Start the Drama Llama web UI.
#
# Default: build web/dist if it is missing or stale, then run the FastAPI app
# with its in-process worker (http://127.0.0.1:8770).
#
# --dev runs the Vite dev server on :5173 with HMR and proxies /api to the
# backend on :8770. `serve` auto-starts the Suno bridge sidecar (:9430)
# unless --no-suno-bridge is passed. Chatterbox stays a separate service and
# is not started here.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WEB="$ROOT/web"
DIST="$WEB/dist"

HOST="${DRAMA_LLAMA_HOST:-${DRAMA_STUDIO_HOST:-127.0.0.1}}"
PORT="${DRAMA_LLAMA_PORT:-${DRAMA_STUDIO_PORT:-8770}}"
BIN="${DRAMA_LLAMA_BIN:-${DRAMA_STUDIO_BIN:-}}"

MODE="serve"
BUILD_MODE="auto"   # auto | always | never
OPEN_BROWSER=0
EXTRA=()

usage() {
  cat <<'EOF'
Usage: ./start-ui.sh [options] [-- serve options]

Builds web/dist when needed, then starts Drama Llama.

Options:
  -d, --dev          Run Vite dev server (:5173) + API (:8770) with HMR
  -b, --build        Force a web rebuild before serving
      --no-build     Never build; fail if web/dist is missing
  -o, --open         Open the UI in the default browser once it is ready
      --host HOST    Bind address (default: 127.0.0.1)
      --port PORT    API port (default: 8770)
  -h, --help         Show this help

Any other arguments are passed to `drama-llama serve`, e.g. --no-worker.
Environment: DRAMA_LLAMA_BIN, DRAMA_LLAMA_HOST, DRAMA_LLAMA_PORT
(the legacy DRAMA_STUDIO_* names are still honoured).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -d|--dev)      MODE="dev"; shift ;;
    -b|--build)    BUILD_MODE="always"; shift ;;
    --no-build)    BUILD_MODE="never"; shift ;;
    -o|--open)     OPEN_BROWSER=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    --host)        [[ $# -ge 2 ]] || { echo "error: --host needs a value" >&2; exit 2; }
                   HOST="$2"; shift 2 ;;
    --host=*)      HOST="${1#*=}"; shift ;;
    --port)        [[ $# -ge 2 ]] || { echo "error: --port needs a value" >&2; exit 2; }
                   PORT="$2"; shift 2 ;;
    --port=*)      PORT="${1#*=}"; shift ;;
    *)             EXTRA+=("$1"); shift ;;
  esac
done

# 0.0.0.0 is a bind address, not something a browser can open.
BROWSE_HOST="$HOST"
case "$HOST" in
  0.0.0.0|::|"[::]") BROWSE_HOST="127.0.0.1" ;;
esac
BASE_URL="http://$BROWSE_HOST:$PORT"

if [[ -z "$BIN" ]]; then
  if command -v drama-llama >/dev/null 2>&1; then
    BIN="$(command -v drama-llama)"
  elif [[ -x "$HOME/.venvs/qwen3tts/bin/drama-llama" ]]; then
    BIN="$HOME/.venvs/qwen3tts/bin/drama-llama"
  elif command -v drama-studio >/dev/null 2>&1; then
    BIN="$(command -v drama-studio)"
  else
    BIN="$HOME/.venvs/qwen3tts/bin/drama-studio"
  fi
fi

if [[ ! -x "$BIN" ]]; then
  cat >&2 <<EOF
error: drama-llama not found at $BIN

Install the package into the TTS venv first:
  ~/.venvs/qwen3tts/bin/pip install -e "$ROOT" --no-deps

Or point DRAMA_LLAMA_BIN at another installation.
EOF
  exit 1
fi

require_npm() {
  if ! command -v npm >/dev/null 2>&1; then
    echo "error: npm is required to build or run the web UI" >&2
    exit 1
  fi
}

ensure_node_modules() {
  require_npm
  if [[ ! -d "$WEB/node_modules" ]]; then
    echo "Installing web dependencies..."
    (cd "$WEB" && npm install)
  fi
}

needs_build() {
  [[ -f "$DIST/index.html" ]] || return 0
  local newer
  newer="$(find "$WEB/src" "$WEB/public" "$WEB/index.html" "$WEB/package.json" "$WEB/vite.config.ts" \
    "$WEB/tsconfig.json" -newer "$DIST/index.html" -print -quit 2>/dev/null)"
  [[ -n "$newer" ]]
}

build_web() {
  ensure_node_modules
  echo "Building web UI..."
  (cd "$WEB" && npm run build)
}

# Poll /api/health, then open the UI. Runs in the background so the server can
# start in the foreground.
open_when_ready() {
  local url="$1" health="$2"
  command -v curl >/dev/null 2>&1 || return 0
  command -v xdg-open >/dev/null 2>&1 || return 0
  (
    for _ in $(seq 1 120); do
      if curl -fsS --max-time 2 -o /dev/null "$health" 2>/dev/null; then
        xdg-open "$url" >/dev/null 2>&1 || true
        return 0
      fi
      sleep 0.5
    done
  ) &
}

# True when a Drama Llama API already answers on the configured port.
api_is_running() {
  command -v curl >/dev/null 2>&1 || return 1
  local body
  body="$(curl -fsS --max-time 2 "$BASE_URL/api/health" 2>/dev/null)" || return 1
  [[ "$body" == *'"version"'* ]]
}

if api_is_running; then
  if [[ "$MODE" == "serve" ]]; then
    echo "Drama Llama is already running at $BASE_URL"
    if [[ "$OPEN_BROWSER" == "1" ]] && command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$BASE_URL" >/dev/null 2>&1 || true
    fi
    exit 0
  fi
  echo "Reusing the running API at $BASE_URL"
  API_RUNNING=1
else
  API_RUNNING=0
fi

if [[ "$MODE" == "dev" ]]; then
  ensure_node_modules
  (cd "$WEB" && npm run dev) &
  VITE_PID=$!
  trap 'kill "$VITE_PID" 2>/dev/null || true' EXIT INT TERM

  echo "Drama Llama (dev)  UI: http://127.0.0.1:5173  API: $BASE_URL"
  if [[ "$OPEN_BROWSER" == "1" ]]; then
    open_when_ready "http://127.0.0.1:5173" "$BASE_URL/api/health"
  fi
  if [[ "$API_RUNNING" == "1" ]]; then
    wait "$VITE_PID" || true
    exit 0
  fi
  "$BIN" serve --host "$HOST" --port "$PORT" "${EXTRA[@]}"
  exit $?
fi

case "$BUILD_MODE" in
  always) build_web ;;
  auto)
    if needs_build; then
      build_web
    else
      echo "web/dist is up to date."
    fi
    ;;
  never)
    if [[ ! -f "$DIST/index.html" ]]; then
      echo "error: web/dist/index.html is missing and --no-build was given" >&2
      exit 1
    fi
    ;;
esac

echo "Starting Drama Llama at $BASE_URL"
if [[ "$OPEN_BROWSER" == "1" ]]; then
  open_when_ready "$BASE_URL" "$BASE_URL/api/health"
fi
exec "$BIN" serve --host "$HOST" --port "$PORT" "${EXTRA[@]}"
