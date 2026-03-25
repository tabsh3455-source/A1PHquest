#!/usr/bin/env bash
set -euo pipefail

# P16 long-run soak helper:
# - keeps core services up
# - runs continuous health monitoring
# - samples docker resource usage for later threshold analysis

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE="deploy/docker-compose.yml"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-$ROOT_DIR/.env}"
DURATION_SECONDS="${DURATION_SECONDS:-86400}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-60}"
GRACE_SECONDS="${GRACE_SECONDS:-60}"
HEALTH_PROBE_TIMEOUT_SECONDS="${HEALTH_PROBE_TIMEOUT_SECONDS:-3}"
HEALTH_PROBE_RETRIES="${HEALTH_PROBE_RETRIES:-3}"
HEALTH_PROBE_RETRY_DELAY_SECONDS="${HEALTH_PROBE_RETRY_DELAY_SECONDS:-0.5}"
HEALTH_REPORT_FILE="${HEALTH_REPORT_FILE:-deploy/p16_soak_health.json}"
STATS_LOG_FILE="${STATS_LOG_FILE:-deploy/p16_soak_stats.log}"
REPORT_FILE="${REPORT_FILE:-deploy/p16_soak_report.json}"
PROGRESS_FILE="${PROGRESS_FILE:-deploy/p16_soak_progress.json}"
BUILD_IMAGES="${BUILD_IMAGES:-0}"
ARCHIVE_EVIDENCE="${ARCHIVE_EVIDENCE:-0}"
EVIDENCE_DIR="${EVIDENCE_DIR:-deploy/evidence/p16}"
EVIDENCE_LABEL="${EVIDENCE_LABEL:-}"
REQUIRED_SERVICES="${REQUIRED_SERVICES:-a1phquest-api,a1phquest-worker-supervisor,a1phquest-postgres}"
MAX_TOTAL_MEM_MIB="${MAX_TOTAL_MEM_MIB:-3584}"
MAX_CONTAINER_MEM_MIB="${MAX_CONTAINER_MEM_MIB:-1024}"
MAX_CPU_PCT="${MAX_CPU_PCT:-190}"
MAX_PIDS="${MAX_PIDS:-256}"
MAX_DOCKER_ERROR_RATE="${MAX_DOCKER_ERROR_RATE:-0.05}"
started_at_iso="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
started_at_epoch="$(date +%s)"
sample_count=0
health_pid=""

write_progress_file() {
  local status="$1"
  local updated_at elapsed remaining tmp_file
  updated_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  elapsed=$(( $(date +%s) - started_at_epoch ))
  if (( elapsed < 0 )); then
    elapsed=0
  fi
  remaining=$(( DURATION_SECONDS - elapsed ))
  if (( remaining < 0 )); then
    remaining=0
  fi
  tmp_file="${PROGRESS_FILE}.tmp"
  cat > "${tmp_file}" <<EOF
{"status":"${status}","started_at":"${started_at_iso}","updated_at":"${updated_at}","duration_seconds":${DURATION_SECONDS},"elapsed_seconds":${elapsed},"remaining_seconds":${remaining},"samples_collected":${sample_count},"health_report":"${HEALTH_REPORT_FILE}","stats_log":"${STATS_LOG_FILE}","report":"${REPORT_FILE}"}
EOF
  mv "${tmp_file}" "${PROGRESS_FILE}"
}

finalize_progress_file() {
  local final_status="${1:-completed}"
  trap - ERR
  write_progress_file "${final_status}"
}

on_error() {
  local line_no="$1"
  echo "[p16-soak] failed at line ${line_no}"
  finalize_progress_file "failed"
}

on_terminate() {
  echo "[p16-soak] received stop signal, cleaning up monitor"
  if [[ -n "${health_pid}" ]] && kill -0 "${health_pid}" >/dev/null 2>&1; then
    kill "${health_pid}" >/dev/null 2>&1 || true
    wait "${health_pid}" >/dev/null 2>&1 || true
  fi
  finalize_progress_file "interrupted"
  exit 130
}

trap 'on_error ${LINENO}' ERR
trap 'on_terminate' INT TERM

echo "[p16-soak] starting stack"
if [[ "${BUILD_IMAGES}" == "1" ]]; then
  docker compose --env-file "${COMPOSE_ENV_FILE}" -f "${COMPOSE_FILE}" up --build -d postgres worker-supervisor api backup
else
  docker compose --env-file "${COMPOSE_ENV_FILE}" -f "${COMPOSE_FILE}" up -d postgres worker-supervisor api backup
fi

echo "[p16-soak] monitoring health for ${DURATION_SECONDS}s"
write_progress_file "running"
python3 deploy/health_monitor.py \
  --duration-seconds "${DURATION_SECONDS}" \
  --interval-seconds 5 \
  --grace-seconds "${GRACE_SECONDS}" \
  --probe-timeout-seconds "${HEALTH_PROBE_TIMEOUT_SECONDS}" \
  --probe-retries "${HEALTH_PROBE_RETRIES}" \
  --probe-retry-delay-seconds "${HEALTH_PROBE_RETRY_DELAY_SECONDS}" \
  > "${HEALTH_REPORT_FILE}" &
health_pid=$!

echo "[p16-soak] sampling docker stats every ${INTERVAL_SECONDS}s"
: > "${STATS_LOG_FILE}"
deadline=$(( $(date +%s) + DURATION_SECONDS ))
while (( $(date +%s) < deadline )); do
  timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if docker stats --no-stream --format "${timestamp}|{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.PIDs}}" >> "${STATS_LOG_FILE}" 2>/dev/null; then
    :
  else
    echo "${timestamp}|error|docker_stats_failed" >> "${STATS_LOG_FILE}"
  fi
  sample_count=$(( sample_count + 1 ))
  write_progress_file "running"
  elapsed_seconds=$(( $(date +%s) - started_at_epoch ))
  remaining_seconds=$(( DURATION_SECONDS - elapsed_seconds ))
  if (( remaining_seconds < 0 )); then
    remaining_seconds=0
  fi
  echo "[p16-soak] progress elapsed=${elapsed_seconds}s remaining=${remaining_seconds}s samples=${sample_count}"
  sleep "${INTERVAL_SECONDS}"
done

wait "${health_pid}"

python3 deploy/p16_soak_analyze.py \
  --health-report "${HEALTH_REPORT_FILE}" \
  --stats-log "${STATS_LOG_FILE}" \
  --report "${REPORT_FILE}" \
  --duration-seconds "${DURATION_SECONDS}" \
  --stats-interval-seconds "${INTERVAL_SECONDS}" \
  --required-services "${REQUIRED_SERVICES}" \
  --max-total-mem-mib "${MAX_TOTAL_MEM_MIB}" \
  --max-container-mem-mib "${MAX_CONTAINER_MEM_MIB}" \
  --max-cpu-pct "${MAX_CPU_PCT}" \
  --max-pids "${MAX_PIDS}" \
  --max-docker-error-rate "${MAX_DOCKER_ERROR_RATE}"

if [[ "${ARCHIVE_EVIDENCE}" == "1" ]]; then
  echo "[p16-soak] archiving soak evidence"
  archive_cmd=(
    python3 deploy/p16_archive_evidence.py
    --soak-report "${REPORT_FILE}"
    --health-report "${HEALTH_REPORT_FILE}"
    --stats-log "${STATS_LOG_FILE}"
    --evidence-dir "${EVIDENCE_DIR}"
  )
  if [[ -n "${EVIDENCE_LABEL}" ]]; then
    archive_cmd+=(--label "${EVIDENCE_LABEL}")
  fi
  "${archive_cmd[@]}"
fi

finalize_progress_file "completed"
echo "[p16-soak] done. report: ${REPORT_FILE}"
