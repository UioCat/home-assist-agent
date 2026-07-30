#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$MODULE_DIR/backend"
WEB_DIR="$MODULE_DIR/web"

: "${PWCLI:?Set PWCLI to the playwright-cli binary or bundled wrapper script}"

command -v curl >/dev/null 2>&1
command -v npm >/dev/null 2>&1
command -v uv >/dev/null 2>&1

E2E_PORT="${IOT_MCP_E2E_PORT:-18090}"
BASE_URL="http://127.0.0.1:${E2E_PORT}"
E2E_TMP="$(mktemp -d "${TMPDIR:-/tmp}/iot-mcp-browser-e2e.XXXXXX")"
SERVER_LOG="$E2E_TMP/server.log"
SERVER_PID=""
export PLAYWRIGHT_CLI_SESSION="iot-mcp-browser-e2e-$$"

cleanup() {
  set +e
  (
    cd "$E2E_TMP"
    "$PWCLI" close >/dev/null 2>&1
  )
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID"
    wait "$SERVER_PID"
  fi
  rm -rf -- "$E2E_TMP"
}
trap cleanup EXIT INT TERM

(
  cd "$BACKEND_DIR"
  uv sync --extra dev
)
(
  cd "$WEB_DIR"
  npm ci
  npm run build
)

(
  cd "$BACKEND_DIR"
  export IOT_MCP_SERVER_HOST="127.0.0.1"
  export IOT_MCP_SERVER_PORT="$E2E_PORT"
  export IOT_MCP_WEB_DIST_PATH="$WEB_DIR/dist"
  export IOT_MCP_DATABASE_URL="sqlite+aiosqlite:///$E2E_TMP/browser.db"
  export IOT_MCP_ADMIN_TOKEN="browser-admin-token"
  export IOT_MCP_MACHINE_TOKENS='{"browser-machine-token":"browser-agent"}'
  export IOT_MCP_SESSION_SIGNING_SECRET="browser-session-signing-secret"
  export IOT_MCP_WEBHOOK_SECRET="browser-webhook-secret"
  export IOT_MCP_SECURE_COOKIES="false"
  exec uv run python -m iot_mcp --mode http
) >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

ready=false
for _ in {1..120}; do
  if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    tail -n 100 "$SERVER_LOG"
    exit 1
  fi
  if curl --fail --silent --show-error --output /dev/null "$BASE_URL/"; then
    ready=true
    break
  fi
  sleep 0.25
done
if [[ "$ready" != "true" ]]; then
  tail -n 100 "$SERVER_LOG"
  exit 1
fi

(
  cd "$E2E_TMP"
  "$PWCLI" open "$BASE_URL"
  run_output="$("$PWCLI" run-code --filename "$SCRIPT_DIR/browser_e2e.js" 2>&1)"
  printf '%s\n' "$run_output"
  if [[ "$run_output" == *"### Error"* ]] || [[ ! -s browser-e2e-final.png ]]; then
    exit 1
  fi
)

echo "Browser E2E passed: React dist -> FastAPI -> Mock Provider"
