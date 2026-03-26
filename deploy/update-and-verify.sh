#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="${BRANCH:-main}"
REMOTE="${REMOTE:-origin}"
BUILD_IMAGES="${BUILD_IMAGES:-1}"
DEPLOY_NGINX="${DEPLOY_NGINX:-0}"
RUN_SMOKE_TEST="${RUN_SMOKE_TEST:-1}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-300}"
SMOKE_TEST_API_BASE="${SMOKE_TEST_API_BASE:-http://127.0.0.1:8000}"
SMOKE_TEST_API_VERIFY_SSL="${SMOKE_TEST_API_VERIFY_SSL:-0}"

log() {
  printf '[update-verify] %s\n' "$*"
}

warn() {
  printf '[update-verify] WARNING: %s\n' "$*" >&2
}

fail() {
  printf '[update-verify] ERROR: %s\n' "$*" >&2
  printf '\n[update-verify] Rollback hint:\n' >&2
  printf 'cd %s\n' "$ROOT_DIR" >&2
  printf 'git reflog -n 5\n' >&2
  printf 'git reset --hard <previous-commit>\n' >&2
  printf 'BUILD_IMAGES=0 bash deploy/update-from-github.sh\n' >&2
  exit 1
}

if [[ "$(uname -s)" != "Linux" ]]; then
  fail "This script supports Linux hosts only."
fi

wait_for_url() {
  local url="$1"
  local insecure="${2:-0}"
  local start_ts
  start_ts="$(date +%s)"
  while true; do
    if [ "$insecure" = "1" ]; then
      if curl -kfsS --max-time 5 "$url" >/dev/null 2>&1; then
        return 0
      fi
    else
      if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then
        return 0
      fi
    fi
    if [ $(( "$(date +%s)" - start_ts )) -ge "$WAIT_TIMEOUT_SECONDS" ]; then
      return 1
    fi
    sleep 2
  done
}

cd "$ROOT_DIR"

log "Updating code and restarting stack from ${REMOTE}/${BRANCH}..."
BRANCH="$BRANCH" REMOTE="$REMOTE" BUILD_IMAGES="$BUILD_IMAGES" DEPLOY_NGINX="$DEPLOY_NGINX" bash "$ROOT_DIR/deploy/update-from-github.sh"

log "Checking API health..."
wait_for_url "http://127.0.0.1:8000/healthz" || fail "API health check timeout"

log "Checking frontend health..."
wait_for_url "http://127.0.0.1:5173/" || fail "Frontend health check timeout"

if [ "$DEPLOY_NGINX" = "1" ]; then
  log "Checking nginx HTTPS health..."
  wait_for_url "https://127.0.0.1/" "1" || fail "Nginx HTTPS check timeout"
fi

if [ "$RUN_SMOKE_TEST" = "1" ]; then
  if command -v python3 >/dev/null 2>&1; then
    log "Running API smoke test..."
    SMOKE_TEST_API_BASE="$SMOKE_TEST_API_BASE" SMOKE_TEST_API_VERIFY_SSL="$SMOKE_TEST_API_VERIFY_SSL" python3 "$ROOT_DIR/deploy/smoke_test_api.py" || fail "smoke_test_api.py failed"
  else
    warn "python3 not found, skipping smoke_test_api.py"
  fi
fi

log "Deployment update verification passed."
