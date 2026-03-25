#!/usr/bin/env bash
set -euo pipefail

# Background controller for P16 soak on Linux VPS.
# Usage:
#   bash deploy/p16_soak_background.sh start
#   bash deploy/p16_soak_background.sh status
#   bash deploy/p16_soak_background.sh stop
#   bash deploy/p16_soak_background.sh logs
#   bash deploy/p16_soak_background.sh finalize
#   bash deploy/p16_soak_background.sh watch-start
#   bash deploy/p16_soak_background.sh watch-status
#   bash deploy/p16_soak_background.sh watch-stop

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PID_FILE="${PID_FILE:-deploy/p16_soak.pid}"
LOG_FILE="${LOG_FILE:-deploy/p16_soak.log}"
PROGRESS_FILE="${PROGRESS_FILE:-deploy/p16_soak_progress.json}"
TAIL_LINES="${TAIL_LINES:-60}"
DURATION_SECONDS="${DURATION_SECONDS:-86400}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-60}"
BUILD_IMAGES="${BUILD_IMAGES:-0}"
ARCHIVE_EVIDENCE="${ARCHIVE_EVIDENCE:-1}"
EVIDENCE_LABEL="${EVIDENCE_LABEL:-vps-soak}"
STARTUP_CHECK_SECONDS="${STARTUP_CHECK_SECONDS:-2}"
AUTO_WATCH_START="${AUTO_WATCH_START:-1}"
WATCH_PID_FILE="${WATCH_PID_FILE:-deploy/p16_auto_finalize.pid}"
WATCH_LOG_FILE="${WATCH_LOG_FILE:-deploy/p16_auto_finalize.log}"
WATCH_STATUS_FILE="${WATCH_STATUS_FILE:-deploy/p16_auto_finalize.status.json}"
WATCH_CHECK_INTERVAL_SECONDS="${WATCH_CHECK_INTERVAL_SECONDS:-60}"
WATCH_MAX_WAIT_SECONDS="${WATCH_MAX_WAIT_SECONDS:-200000}"
WATCH_HEARTBEAT_STALE_SECONDS="${WATCH_HEARTBEAT_STALE_SECONDS:-180}"
WATCH_STARTUP_CHECK_SECONDS="${WATCH_STARTUP_CHECK_SECONDS:-2}"
WATCH_SOAK_STATUS_CMD="${WATCH_SOAK_STATUS_CMD:-bash deploy/p16_soak_background.sh status}"
WATCH_SOAK_FINALIZE_CMD="${WATCH_SOAK_FINALIZE_CMD:-bash deploy/p16_soak_background.sh finalize}"
CMD="${1:-status}"
WATCHER_RUNNING_PID=""

is_running() {
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
  local cmdline
  cmdline="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
  if [[ -z "${cmdline}" ]]; then
    return 1
  fi
  # Avoid PID reuse false-positives by validating the tracked process command.
  case "${cmdline}" in
    *"deploy/p16_soak.sh"*) ;;
    *) return 1 ;;
  esac
  return 0
}

is_watcher_running() {
  WATCHER_RUNNING_PID=""
  local pid
  pid="$(cat "${WATCH_PID_FILE}" 2>/dev/null || true)"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
    WATCHER_RUNNING_PID="${pid}"
    return 0
  fi

  # Fallback to watcher status file for self-healing when pid marker was lost.
  if [[ -f "${WATCH_STATUS_FILE}" ]]; then
    pid="$(python3 - "${WATCH_STATUS_FILE}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)
print(str(payload.get("pid") or "").strip())
PY
)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
      WATCHER_RUNNING_PID="${pid}"
      echo "${pid}" > "${WATCH_PID_FILE}"
      return 0
    fi
  fi
  return 1
}

watch_last_action() {
  if [[ ! -f "${WATCH_STATUS_FILE}" ]]; then
    echo ""
    return 0
  fi
  python3 - "${WATCH_STATUS_FILE}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)
print(str(payload.get("last_action") or "").strip())
PY
}

start_watcher() {
  if is_watcher_running; then
    echo "auto-finalize watcher already running (pid=${WATCHER_RUNNING_PID})"
    return 0
  fi
  rm -f "${WATCH_PID_FILE}" || true
  # Launch watcher detached so soak finalization survives shell/session exits.
  nohup env \
    LOG_FILE="${WATCH_LOG_FILE}" \
    PID_FILE="${WATCH_PID_FILE}" \
    STATUS_FILE="${WATCH_STATUS_FILE}" \
    PROGRESS_FILE="${PROGRESS_FILE}" \
    CHECK_INTERVAL_SECONDS="${WATCH_CHECK_INTERVAL_SECONDS}" \
    MAX_WAIT_SECONDS="${WATCH_MAX_WAIT_SECONDS}" \
    HEARTBEAT_STALE_SECONDS="${WATCH_HEARTBEAT_STALE_SECONDS}" \
    SOAK_STATUS_CMD="${WATCH_SOAK_STATUS_CMD}" \
    SOAK_FINALIZE_CMD="${WATCH_SOAK_FINALIZE_CMD}" \
    bash deploy/p16_auto_finalize_watch.sh >> "${WATCH_LOG_FILE}" 2>&1 &
  local pid=$!
  sleep "${WATCH_STARTUP_CHECK_SECONDS}"
  if ! kill -0 "${pid}" >/dev/null 2>&1; then
    local action
    action="$(watch_last_action)"
    if [[ "${action}" == "finalize_completed" ]]; then
      echo "auto-finalize watcher completed immediately (soak already completed)"
      echo "status: ${WATCH_STATUS_FILE}"
      return 0
    fi
    echo "auto-finalize watcher failed to stay alive after startup check (${WATCH_STARTUP_CHECK_SECONDS}s)"
    if [[ -f "${WATCH_LOG_FILE}" ]]; then
      echo "--- watcher recent log ---"
      tail -n "${TAIL_LINES}" "${WATCH_LOG_FILE}" || true
      echo "--- end watcher log ---"
    fi
    return 1
  fi
  echo "auto-finalize watcher started (pid=${pid})"
  echo "log: ${WATCH_LOG_FILE}"
  echo "status: ${WATCH_STATUS_FILE}"
}

watch_status() {
  local running=0
  if is_watcher_running; then
    local pid elapsed
    pid="${WATCHER_RUNNING_PID}"
    elapsed="$(ps -o etimes= -p "${pid}" 2>/dev/null | tr -d '[:space:]' || true)"
    echo "auto-finalize watcher running (pid=${pid})"
    if [[ -n "${elapsed}" ]]; then
      echo "watcher_elapsed_seconds=${elapsed}"
    fi
    running=1
  else
    if [[ -f "${WATCH_PID_FILE}" ]]; then
      rm -f "${WATCH_PID_FILE}" || true
    fi
    echo "auto-finalize watcher not running"
  fi

  if [[ -f "${WATCH_STATUS_FILE}" ]]; then
    echo "status: ${WATCH_STATUS_FILE}"
    python3 - "${WATCH_STATUS_FILE}" "${WATCH_HEARTBEAT_STALE_SECONDS}" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

status_path = Path(sys.argv[1])
stale_seconds = int(sys.argv[2])
try:
    payload = json.loads(status_path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"watcher_status_parse_error={exc}")
    raise SystemExit(0)

started = str(payload.get("started_at") or "").strip()
updated = str(payload.get("updated_at") or "").strip()
elapsed = payload.get("elapsed_seconds")
action = str(payload.get("last_action") or "").strip()
soak_state = str(payload.get("soak_state") or "").strip()
if started:
    print(f"watcher_started_at={started}")
if updated:
    print(f"watcher_updated_at={updated}")
if elapsed is not None:
    print(f"watcher_elapsed_seconds={elapsed}")
if action:
    print(f"watcher_last_action={action}")
if soak_state:
    print(f"watcher_soak_state={soak_state}")

if updated:
    try:
        updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        if updated_dt.tzinfo is None:
            updated_dt = updated_dt.replace(tzinfo=timezone.utc)
        age = int(max((datetime.now(timezone.utc) - updated_dt.astimezone(timezone.utc)).total_seconds(), 0))
        print(f"watcher_heartbeat_age_seconds={age}")
        print(f"watcher_heartbeat_stale={str(age > stale_seconds).lower()}")
    except Exception:
        print("watcher_heartbeat_age_seconds=unknown")
PY
  else
    echo "status file not found: ${WATCH_STATUS_FILE}"
  fi

  echo "log: ${WATCH_LOG_FILE}"
  if (( running == 1 )); then
    return 0
  fi
  return 1
}

stop_watcher() {
  if ! is_watcher_running; then
    echo "auto-finalize watcher not running"
    rm -f "${WATCH_PID_FILE}" || true
    return 0
  fi
  local pid
  pid="${WATCHER_RUNNING_PID}"
  kill "${pid}" >/dev/null 2>&1 || true
  sleep 1
  if kill -0 "${pid}" >/dev/null 2>&1; then
    kill -9 "${pid}" >/dev/null 2>&1 || true
  fi
  rm -f "${WATCH_PID_FILE}" || true
  echo "auto-finalize watcher stopped"
}

start_soak() {
  if is_running; then
    echo "p16 soak is already running (pid=$(cat "${PID_FILE}"))"
    return 0
  fi
  # Remove stale PID marker before launching a new run.
  rm -f "${PID_FILE}" || true
  # Launch soak detached from current shell so long-run check survives SSH/session exits.
  nohup env \
    DURATION_SECONDS="${DURATION_SECONDS}" \
    INTERVAL_SECONDS="${INTERVAL_SECONDS}" \
    BUILD_IMAGES="${BUILD_IMAGES}" \
    ARCHIVE_EVIDENCE="${ARCHIVE_EVIDENCE}" \
    EVIDENCE_LABEL="${EVIDENCE_LABEL}" \
    bash deploy/p16_soak.sh > "${LOG_FILE}" 2>&1 &
  local pid=$!
  echo "${pid}" > "${PID_FILE}"
  sleep "${STARTUP_CHECK_SECONDS}"
  if ! kill -0 "${pid}" >/dev/null 2>&1; then
    echo "p16 soak failed to stay alive after startup check (${STARTUP_CHECK_SECONDS}s)"
    rm -f "${PID_FILE}" || true
    if [[ -f "${LOG_FILE}" ]]; then
      echo "--- recent log ---"
      tail -n "${TAIL_LINES}" "${LOG_FILE}" || true
      echo "--- end log ---"
    fi
    return 1
  fi
  echo "p16 soak started (pid=${pid})"
  echo "log: ${LOG_FILE}"
  if [[ "${AUTO_WATCH_START}" == "1" ]]; then
    start_watcher
  fi
}

status_soak() {
  if is_running; then
    local pid elapsed
    pid="$(cat "${PID_FILE}")"
    elapsed="$(ps -o etimes= -p "${pid}" 2>/dev/null | tr -d '[:space:]' || true)"
    echo "p16 soak running (pid=${pid})"
    if [[ -n "${elapsed}" ]]; then
      echo "elapsed_seconds=${elapsed}"
    fi
    echo "log: ${LOG_FILE}"
    if [[ -f "${PROGRESS_FILE}" ]]; then
      echo "progress: ${PROGRESS_FILE}"
      python3 - "${PROGRESS_FILE}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"progress_parse_error={exc}")
    raise SystemExit(0)
status = payload.get("status", "unknown")
elapsed = payload.get("elapsed_seconds")
remaining = payload.get("remaining_seconds")
samples = payload.get("samples_collected")
updated = payload.get("updated_at")
print(f"progress_status={status}")
if elapsed is not None:
    print(f"progress_elapsed_seconds={elapsed}")
if remaining is not None:
    print(f"progress_remaining_seconds={remaining}")
if samples is not None:
    print(f"progress_samples={samples}")
if updated:
    print(f"progress_updated_at={updated}")
PY
    fi
    return 0
  fi
  if [[ -f "${PID_FILE}" ]]; then
    # Clean stale PID marker so next start will not report a ghost process.
    rm -f "${PID_FILE}" || true
  fi
  echo "p16 soak not running"
  return 1
}

stop_soak() {
  if ! is_running; then
    echo "p16 soak not running"
    rm -f "${PID_FILE}" || true
    return 0
  fi
  local pid
  pid="$(cat "${PID_FILE}")"
  kill "${pid}" >/dev/null 2>&1 || true
  sleep 1
  if kill -0 "${pid}" >/dev/null 2>&1; then
    kill -9 "${pid}" >/dev/null 2>&1 || true
  fi
  rm -f "${PID_FILE}" || true
  echo "p16 soak stopped"
}

logs_soak() {
  if [[ ! -f "${LOG_FILE}" ]]; then
    echo "log file not found: ${LOG_FILE}"
    return 1
  fi
  tail -n "${TAIL_LINES}" "${LOG_FILE}"
}

finalize_soak() {
  # Finalization requires soak to be fully completed.
  if is_running; then
    echo "p16 soak is still running; finalize is blocked"
    return 1
  fi
  python3 deploy/p16_finalize_after_soak.py
}

case "${CMD}" in
  start)
    start_soak
    ;;
  status)
    status_soak
    ;;
  stop)
    stop_soak
    ;;
  logs)
    logs_soak
    ;;
  finalize)
    finalize_soak
    ;;
  watch-start)
    start_watcher
    ;;
  watch-status)
    watch_status
    ;;
  watch-stop)
    stop_watcher
    ;;
  *)
    echo "Usage: $0 {start|status|stop|logs|finalize|watch-start|watch-status|watch-stop}"
    exit 2
    ;;
esac
