#!/usr/bin/env bash
set -euo pipefail

# Background watcher:
# - waits until p16 soak background run finishes
# - triggers finalize pipeline once

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CHECK_INTERVAL_SECONDS="${CHECK_INTERVAL_SECONDS:-60}"
LOG_FILE="${LOG_FILE:-deploy/p16_auto_finalize.log}"
PID_FILE="${PID_FILE:-deploy/p16_auto_finalize.pid}"
STATUS_FILE="${STATUS_FILE:-deploy/p16_auto_finalize.status.json}"
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-200000}"
HEARTBEAT_STALE_SECONDS="${HEARTBEAT_STALE_SECONDS:-180}"
PROGRESS_FILE="${PROGRESS_FILE:-deploy/p16_soak_progress.json}"
SOAK_STATUS_CMD="${SOAK_STATUS_CMD:-bash deploy/p16_soak_background.sh status}"
SOAK_FINALIZE_CMD="${SOAK_FINALIZE_CMD:-bash deploy/p16_soak_background.sh finalize}"
started_at="$(date +%s)"
started_at_iso="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

is_watcher_running() {
  if [[ ! -f "${PID_FILE}" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ -z "${pid}" ]]; then
    return 1
  fi
  if ! kill -0 "${pid}" >/dev/null 2>&1; then
    return 1
  fi
  return 0
}

status_age_seconds() {
  if [[ ! -f "${STATUS_FILE}" ]]; then
    echo ""
    return 0
  fi
  python3 - "${STATUS_FILE}" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)
text = str(payload.get("updated_at") or "").strip()
if not text:
    print("")
    raise SystemExit(0)
try:
    updated = datetime.fromisoformat(text.replace("Z", "+00:00"))
except ValueError:
    print("")
    raise SystemExit(0)
if updated.tzinfo is None:
    updated = updated.replace(tzinfo=timezone.utc)
now = datetime.now(timezone.utc)
age = int(max((now - updated.astimezone(timezone.utc)).total_seconds(), 0))
print(str(age))
PY
}

write_status() {
  ensure_pid_file
  local action="${1:-unknown}"
  local soak_state="${2:-unknown}"
  local elapsed
  elapsed=$(( $(date +%s) - started_at ))
  local now_iso
  now_iso="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local tmp_file
  tmp_file="${STATUS_FILE}.tmp.$$"
  cat > "${tmp_file}" <<JSON
{"pid":$$,"pid_file":"${PID_FILE}","started_at":"${started_at_iso}","updated_at":"${now_iso}","elapsed_seconds":${elapsed},"last_action":"${action}","soak_state":"${soak_state}","check_interval_seconds":${CHECK_INTERVAL_SECONDS},"max_wait_seconds":${MAX_WAIT_SECONDS},"heartbeat_stale_seconds":${HEARTBEAT_STALE_SECONDS},"progress_file":"${PROGRESS_FILE}"}
JSON
  mv "${tmp_file}" "${STATUS_FILE}"
}

ensure_pid_file() {
  local tracked_pid
  tracked_pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ "${tracked_pid}" != "$$" ]]; then
    echo "$$" > "${PID_FILE}"
  fi
}

is_soak_running_by_progress() {
  python3 - "${PROGRESS_FILE}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(1)
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
status = str(payload.get("status") or "").strip().lower()
raise SystemExit(0 if status == "running" else 1)
PY
}

if is_watcher_running; then
  existing_pid="$(cat "${PID_FILE}")"
  stale_age="$(status_age_seconds)"
  if [[ -n "${stale_age}" ]] && (( stale_age > HEARTBEAT_STALE_SECONDS )); then
    echo "auto-finalize watcher stale (pid=${existing_pid}, age=${stale_age}s), restarting"
    kill "${existing_pid}" >/dev/null 2>&1 || true
    sleep 1
    if kill -0 "${existing_pid}" >/dev/null 2>&1; then
      kill -9 "${existing_pid}" >/dev/null 2>&1 || true
    fi
    rm -f "${PID_FILE}" || true
  else
    echo "auto-finalize watcher already running (pid=${existing_pid})"
    exit 0
  fi
fi

ensure_pid_file
cleanup_pid_file() {
  if [[ ! -f "${PID_FILE}" ]]; then
    return 0
  fi
  local tracked_pid
  tracked_pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  # Remove pid file only when this process still owns it, so concurrent watcher
  # restarts do not accidentally delete the successor pid marker.
  if [[ "${tracked_pid}" == "$$" ]]; then
    rm -f "${PID_FILE}" >/dev/null 2>&1 || true
  fi
}
trap cleanup_pid_file EXIT

{
  echo "[auto-finalize] started at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  write_status "started" "unknown"
  while true; do
    elapsed=$(( $(date +%s) - started_at ))
    if (( elapsed > MAX_WAIT_SECONDS )); then
      write_status "timeout" "unknown"
      echo "[auto-finalize] timeout reached (${MAX_WAIT_SECONDS}s), stop watching"
      exit 1
    fi

    if bash -lc "${SOAK_STATUS_CMD}" >/dev/null 2>&1; then
      write_status "soak_running" "running"
      echo "[auto-finalize] soak still running, elapsed=${elapsed}s"
      sleep "${CHECK_INTERVAL_SECONDS}"
      continue
    fi
    if is_soak_running_by_progress; then
      write_status "soak_running_progress_fallback" "running"
      echo "[auto-finalize] soak still running (progress fallback), elapsed=${elapsed}s"
      sleep "${CHECK_INTERVAL_SECONDS}"
      continue
    fi

    write_status "finalize_started" "completed_or_stopped"
    echo "[auto-finalize] soak not running, execute finalize"
    if bash -lc "${SOAK_FINALIZE_CMD}"; then
      write_status "finalize_completed" "completed"
      echo "[auto-finalize] finalize completed"
      exit 0
    fi
    write_status "finalize_failed_retry" "completed_or_stopped"
    echo "[auto-finalize] finalize failed, retry in ${CHECK_INTERVAL_SECONDS}s"
    sleep "${CHECK_INTERVAL_SECONDS}"
  done
} >> "${LOG_FILE}" 2>&1
