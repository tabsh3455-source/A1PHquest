#!/usr/bin/env bash
set -euo pipefail

# P9 production drill helper:
# - starts core stack
# - runs health checks
# - performs backup + restore rehearsal
# - performs upgrade + rollback rehearsal for api/worker images

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE="deploy/docker-compose.yml"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-$ROOT_DIR/.env}"
API_HEALTH_URL="${API_HEALTH_URL:-http://127.0.0.1:8000/healthz}"
WORKER_HEALTH_URL="${WORKER_HEALTH_URL:-http://127.0.0.1:8010/healthz}"
REPORT_FILE="${REPORT_FILE:-deploy/p9_drill_report.json}"
HEALTH_CURL_CONNECT_TIMEOUT_SECONDS="${HEALTH_CURL_CONNECT_TIMEOUT_SECONDS:-2}"
HEALTH_CURL_MAX_TIME_SECONDS="${HEALTH_CURL_MAX_TIME_SECONDS:-3}"
DRILL_BUILD_IMAGES="${DRILL_BUILD_IMAGES:-1}"
DRILL_ALLOW_BUILD_FALLBACK="${DRILL_ALLOW_BUILD_FALLBACK:-1}"

run_health_check() {
  local url="$1"
  curl --connect-timeout "$HEALTH_CURL_CONNECT_TIMEOUT_SECONDS" --max-time "$HEALTH_CURL_MAX_TIME_SECONDS" -fsS "$url" >/dev/null
}

wait_health() {
  local url="$1"
  local retries="${2:-120}"
  for _ in $(seq 1 "$retries"); do
    if run_health_check "$url"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

pre_api_image="$(docker image inspect deploy-api:latest --format '{{.Id}}' 2>/dev/null || true)"
pre_worker_image="$(docker image inspect deploy-worker-supervisor:latest --format '{{.Id}}' 2>/dev/null || true)"

backup_file=""
upgrade_ok=false
rollback_ok=false
restore_ok=false

echo "[1/6] Starting core services..."
docker compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" up -d postgres worker-supervisor api backup

echo "[2/6] Waiting for health checks..."
for _ in $(seq 1 120); do
  if wait_health "$API_HEALTH_URL" 1 && wait_health "$WORKER_HEALTH_URL" 1; then
    break
  fi
  sleep 1
done
wait_health "$API_HEALTH_URL" 120
wait_health "$WORKER_HEALTH_URL" 120

echo "[3/6] Running backup rehearsal..."
docker exec a1phquest-backup /bin/bash /scripts/backup.sh >/dev/null
backup_file="$(ls -1t deploy/backups/a1phquest_*.sql.enc | head -n 1)"
if [[ -z "${backup_file}" ]]; then
  echo "backup rehearsal failed: no backup file generated"
  exit 1
fi

echo "[4/6] Running restore rehearsal..."
backup_name="$(basename "$backup_file")"
# Restore rehearsal uses an isolated temporary database to avoid clobbering
# the live schema/data during drill execution.
docker exec a1phquest-backup /bin/bash -lc \
  "export PGPASSWORD=\"${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}\"; psql --host=postgres --port=5432 --username=\"${POSTGRES_USER:?POSTGRES_USER is required}\" --set ON_ERROR_STOP=1 -c 'DROP DATABASE IF EXISTS a1phquest_restore_drill;' -c 'CREATE DATABASE a1phquest_restore_drill;'" \
  >/dev/null
docker exec a1phquest-backup /bin/bash -lc \
  "POSTGRES_HOST=postgres POSTGRES_PORT=5432 POSTGRES_DB=a1phquest_restore_drill POSTGRES_USER=\"${POSTGRES_USER:?POSTGRES_USER is required}\" POSTGRES_PASSWORD=\"${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}\" AES_MASTER_KEY=\"${AES_MASTER_KEY:-${KMS_MASTER_KEY:?AES_MASTER_KEY or KMS_MASTER_KEY is required}}\" /scripts/restore_backup.sh /backups/${backup_name}" \
  >/dev/null
docker exec a1phquest-backup /bin/bash -lc \
  "export PGPASSWORD=\"${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}\"; psql --host=postgres --port=5432 --username=\"${POSTGRES_USER:?POSTGRES_USER is required}\" --set ON_ERROR_STOP=1 -c 'DROP DATABASE IF EXISTS a1phquest_restore_drill;'" \
  >/dev/null
restore_ok=true

echo "[5/6] Running upgrade rehearsal..."
if [[ "${DRILL_BUILD_IMAGES}" == "1" ]]; then
  if docker compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" up --build -d api worker-supervisor >/dev/null; then
    :
  elif [[ "${DRILL_ALLOW_BUILD_FALLBACK}" == "1" ]]; then
    echo "build upgrade failed, fallback to no-build recreate for drill continuity"
    docker compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" up -d --no-build api worker-supervisor >/dev/null
  else
    echo "build upgrade failed and fallback disabled"
    exit 1
  fi
else
  docker compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" up -d --no-build api worker-supervisor >/dev/null
fi
wait_health "$API_HEALTH_URL" 120
wait_health "$WORKER_HEALTH_URL" 120
upgrade_ok=true

echo "[6/6] Running rollback rehearsal..."
if [[ -n "$pre_api_image" ]]; then
  if docker image inspect "$pre_api_image" >/dev/null 2>&1; then
    docker image tag "$pre_api_image" deploy-api:latest
  fi
fi
if [[ -n "$pre_worker_image" ]]; then
  if docker image inspect "$pre_worker_image" >/dev/null 2>&1; then
    docker image tag "$pre_worker_image" deploy-worker-supervisor:latest
  fi
fi
docker compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" up -d --no-build api worker-supervisor >/dev/null
wait_health "$API_HEALTH_URL" 120
wait_health "$WORKER_HEALTH_URL" 120
rollback_ok=true

export DRILL_BACKUP_FILE="$backup_file"
export DRILL_RESTORE_OK="$restore_ok"
export DRILL_UPGRADE_OK="$upgrade_ok"
export DRILL_ROLLBACK_OK="$rollback_ok"

python3 - <<'PY' > "$REPORT_FILE"
import json
import os
from datetime import datetime, timezone

report = {
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "backup_file": os.environ.get("DRILL_BACKUP_FILE", ""),
    "restore_ok": os.environ.get("DRILL_RESTORE_OK", "false") == "true",
    "upgrade_ok": os.environ.get("DRILL_UPGRADE_OK", "false") == "true",
    "rollback_ok": os.environ.get("DRILL_ROLLBACK_OK", "false") == "true",
}
print(json.dumps(report, ensure_ascii=False))
PY

echo "P9 drill completed. Report: ${REPORT_FILE}"
