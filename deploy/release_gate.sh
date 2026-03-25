#!/usr/bin/env bash
set -euo pipefail

# Release candidate gate for VPS/Linux environments.
# This script runs the fixed quality/deploy drills in sequence and writes a
# machine-readable report for audit/runbook attachment.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

REPORT_FILE="${REPORT_FILE:-deploy/release_gate_report.json}"
HEALTH_SECONDS="${HEALTH_SECONDS:-30}"
BUILD_IMAGES="${BUILD_IMAGES:-1}"
GATE_ALLOW_BUILD_FALLBACK="${GATE_ALLOW_BUILD_FALLBACK:-1}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
RUN_SOAK_SMOKE="${RUN_SOAK_SMOKE:-1}"
SOAK_SMOKE_DURATION_SECONDS="${SOAK_SMOKE_DURATION_SECONDS:-20}"
SOAK_SMOKE_INTERVAL_SECONDS="${SOAK_SMOKE_INTERVAL_SECONDS:-5}"
HEALTH_PROBE_TIMEOUT_SECONDS="${HEALTH_PROBE_TIMEOUT_SECONDS:-3}"
HEALTH_PROBE_RETRIES="${HEALTH_PROBE_RETRIES:-3}"
HEALTH_PROBE_RETRY_DELAY_SECONDS="${HEALTH_PROBE_RETRY_DELAY_SECONDS:-0.5}"
oneclick_build_mode="${BUILD_IMAGES}"
oneclick_used_fallback=0

run_step() {
  local name="$1"
  shift
  echo "[gate] ${name}"
  "$@"
}

run_pytest_gate() {
  if command -v pytest >/dev/null 2>&1; then
    pytest -q
    return 0
  fi
  if command -v python3 >/dev/null 2>&1 && python3 -m pytest --version >/dev/null 2>&1; then
    python3 -m pytest -q
    return 0
  fi
  if command -v python >/dev/null 2>&1 && python -m pytest --version >/dev/null 2>&1; then
    python -m pytest -q
    return 0
  fi
  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command "pytest -q"
    return 0
  fi
  echo "pytest is not available in PATH. Install pytest on host and retry." >&2
  return 1
}

run_python_gate() {
  if command -v python3 >/dev/null 2>&1; then
    python3 "$@"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    python "$@"
    return 0
  fi
  echo "python3/python not found for script: $*" >&2
  return 1
}

run_oneclick_gate() {
  local requested_build="${BUILD_IMAGES}"
  local oneclick_cmd
  oneclick_cmd="HEALTH_SECONDS=${HEALTH_SECONDS} BUILD_IMAGES=${requested_build} HEALTH_PROBE_TIMEOUT_SECONDS=${HEALTH_PROBE_TIMEOUT_SECONDS} HEALTH_PROBE_RETRIES=${HEALTH_PROBE_RETRIES} HEALTH_PROBE_RETRY_DELAY_SECONDS=${HEALTH_PROBE_RETRY_DELAY_SECONDS} bash deploy/p5s_oneclick.sh"
  if bash -lc "${oneclick_cmd}"; then
    oneclick_build_mode="${requested_build}"
    return 0
  fi
  if [[ "${requested_build}" == "1" && "${GATE_ALLOW_BUILD_FALLBACK}" == "1" ]]; then
    echo "[gate] oneclick build failed, fallback to BUILD_IMAGES=0 for continuity"
    oneclick_cmd="HEALTH_SECONDS=${HEALTH_SECONDS} BUILD_IMAGES=0 HEALTH_PROBE_TIMEOUT_SECONDS=${HEALTH_PROBE_TIMEOUT_SECONDS} HEALTH_PROBE_RETRIES=${HEALTH_PROBE_RETRIES} HEALTH_PROBE_RETRY_DELAY_SECONDS=${HEALTH_PROBE_RETRY_DELAY_SECONDS} bash deploy/p5s_oneclick.sh"
    bash -lc "${oneclick_cmd}"
    oneclick_build_mode="0"
    oneclick_used_fallback=1
    return 0
  fi
  return 1
}

started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export started_at

run_step "pytest" run_pytest_gate
run_step "db_migrate" bash deploy/db_migrate.sh
run_step "oneclick" run_oneclick_gate
run_step "e2e_okx" run_python_gate deploy/e2e_runtime_flow.py --base-url "${BASE_URL}" --exchange okx
run_step "p9_drill" bash deploy/p9_drill.sh
if [[ "${RUN_SOAK_SMOKE}" == "1" ]]; then
  run_step "p16_soak_smoke" bash -lc "DURATION_SECONDS=${SOAK_SMOKE_DURATION_SECONDS} INTERVAL_SECONDS=${SOAK_SMOKE_INTERVAL_SECONDS} BUILD_IMAGES=0 HEALTH_PROBE_TIMEOUT_SECONDS=${HEALTH_PROBE_TIMEOUT_SECONDS} HEALTH_PROBE_RETRIES=${HEALTH_PROBE_RETRIES} HEALTH_PROBE_RETRY_DELAY_SECONDS=${HEALTH_PROBE_RETRY_DELAY_SECONDS} HEALTH_REPORT_FILE=deploy/p16_soak_smoke_health.json STATS_LOG_FILE=deploy/p16_soak_smoke_stats.log REPORT_FILE=deploy/p16_soak_smoke_report.json PROGRESS_FILE=deploy/p16_soak_smoke_progress.json ARCHIVE_EVIDENCE=0 bash deploy/p16_soak.sh"
fi

ended_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export ended_at
export RUN_SOAK_SMOKE
export oneclick_build_mode
export oneclick_used_fallback

python3 - <<'PY' > "${REPORT_FILE}"
import json
import os

report = {
    "started_at": os.environ.get("started_at"),
    "ended_at": os.environ.get("ended_at"),
    "steps": [
        "pytest -q",
        "bash deploy/db_migrate.sh",
        "HEALTH_SECONDS=<n> BUILD_IMAGES=<0|1> bash deploy/p5s_oneclick.sh",
        "python3 deploy/e2e_runtime_flow.py --exchange okx",
        "bash deploy/p9_drill.sh",
        "DURATION_SECONDS=<n> INTERVAL_SECONDS=<n> bash deploy/p16_soak.sh (optional)",
    ],
    "status": "passed",
    "run_soak_smoke": os.environ.get("RUN_SOAK_SMOKE", "1") == "1",
    "oneclick_build_mode": os.environ.get("oneclick_build_mode", "1"),
    "oneclick_used_build_fallback": os.environ.get("oneclick_used_fallback", "0") == "1",
}
print(json.dumps(report, ensure_ascii=False))
PY

echo "Release gate completed. Report: ${REPORT_FILE}"
