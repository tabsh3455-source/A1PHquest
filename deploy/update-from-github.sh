#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="${BRANCH:-main}"
REMOTE="${REMOTE:-origin}"
BUILD_IMAGES="${BUILD_IMAGES:-1}"
DEPLOY_NGINX="${DEPLOY_NGINX:-0}"

log() {
  printf '[update] %s\n' "$*"
}

fail() {
  printf '[update] ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ "$(uname -s)" != "Linux" ]]; then
  fail "This updater supports Linux hosts only."
fi

command -v git >/dev/null 2>&1 || fail "git is required."

cd "$ROOT_DIR"
[ -d ".git" ] || fail "Current directory is not a git checkout: $ROOT_DIR"

log "Fetching latest code from ${REMOTE}/${BRANCH}..."
git fetch "$REMOTE" "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only "$REMOTE" "$BRANCH"

if [ ! -f "$ROOT_DIR/.env" ]; then
  log ".env not found. install.sh will generate a secure deployment .env automatically."
fi

log "Running installer to apply migrations and restart stack..."
BUILD_IMAGES="$BUILD_IMAGES" DEPLOY_NGINX="$DEPLOY_NGINX" bash "$ROOT_DIR/install.sh"

log "Update complete."
