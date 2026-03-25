#!/usr/bin/env bash
set -euo pipefail

# Apply Alembic migrations to the target revision.
# Default mode runs inside docker compose `api` service so dependency versions
# stay consistent with production image.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="deploy/docker-compose.yml"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-$ROOT_DIR/.env}"
TARGET_REVISION="${1:-head}"
MIGRATE_IN_DOCKER="${MIGRATE_IN_DOCKER:-1}"

cd "$ROOT_DIR"

if [ "$MIGRATE_IN_DOCKER" = "1" ]; then
  # Prefer running container so migration uses the exact runtime environment.
  if docker ps --format '{{.Names}}' | grep -q '^a1phquest-api$'; then
    docker exec a1phquest-api python -m app.migrate "$TARGET_REVISION"
    exit 0
  fi

  # Fallback path runs dedicated migrate service to avoid touching api/worker
  # dependency graph during long-run soak or runtime drills.
  # We also avoid sourcing `.env` in shell because values like
  # `APP_NAME=A1phquest API` contain spaces.
  docker compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" run --rm migrate \
    python -m app.migrate "$TARGET_REVISION"
else
  (
    cd apps/api
    python -m app.migrate "$TARGET_REVISION"
  )
fi
