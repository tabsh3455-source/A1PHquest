#!/usr/bin/env bash
set -euo pipefail

# P17-P20 merged execution gate:
# P17: security hardening checks
# P18: Lighter reconcile hardening checks
# P19: observability/ops checks
# P20: release-candidate strict gate
#
# This script is intentionally fail-fast. A JSON report is always emitted on exit.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

REPORT_FILE="${REPORT_FILE:-deploy/p17_p20_merged_report.json}"
SOAK_PROGRESS="${SOAK_PROGRESS:-deploy/p16_soak_progress.json}"
RELEASE_GATE_REPORT="${RELEASE_GATE_REPORT:-deploy/release_gate_report.json}"
P16_ACCEPTANCE_REPORT="${P16_ACCEPTANCE_REPORT:-deploy/p16_acceptance_report.json}"
P20_RC_REPORT="${P20_RC_REPORT:-deploy/p20_rc_report.json}"
P20_CHECKLIST_REPORT="${P20_CHECKLIST_REPORT:-deploy/p20_release_checklist.json}"
P20_RELEASE_MANIFEST="${P20_RELEASE_MANIFEST:-deploy/p20_release_manifest.json}"

RUN_P17_TESTS="${RUN_P17_TESTS:-1}"
RUN_P18_TESTS="${RUN_P18_TESTS:-1}"
RUN_P19_TESTS="${RUN_P19_TESTS:-1}"
RUN_DB_MIGRATE="${RUN_DB_MIGRATE:-1}"
RUN_RELEASE_GATE="${RUN_RELEASE_GATE:-1}"
RUN_P20_RC="${RUN_P20_RC:-1}"
RUN_P20_MANIFEST="${RUN_P20_MANIFEST:-1}"

# auto: strict only when soak has completed; 0/1: force mode
P20_CHECKLIST_STRICT_MODE="${P20_CHECKLIST_STRICT_MODE:-auto}"
# auto: run acceptance inside p20 gate only when strict mode is enabled.
P20_ACCEPTANCE_MODE="${P20_ACCEPTANCE_MODE:-auto}"
# By default merged gate already runs release_gate.sh, so nested P20 release gate is disabled.
P20_RUN_RELEASE_GATE="${P20_RUN_RELEASE_GATE:-0}"
P20_RUN_CHECKLIST="${P20_RUN_CHECKLIST:-1}"

started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
ended_at=""
current_step="init"
exit_code=0

p17_status="skipped"
p18_status="skipped"
p19_status="skipped"
db_migrate_status="skipped"
release_gate_status="skipped"
p20_rc_status="skipped"
p20_manifest_status="skipped"

resolved_soak_status="unknown"
resolved_checklist_strict="0"
resolved_acceptance_mode="0"
strict_decision_reason=""

run_pytest_target() {
  local -a files=("$@")
  if command -v pytest >/dev/null 2>&1; then
    pytest -q "${files[@]}"
    return 0
  fi
  if command -v py >/dev/null 2>&1 && py -m pytest --version >/dev/null 2>&1; then
    py -m pytest -q "${files[@]}"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1 && python3 -m pytest --version >/dev/null 2>&1; then
    python3 -m pytest -q "${files[@]}"
    return 0
  fi
  if command -v python >/dev/null 2>&1 && python -m pytest --version >/dev/null 2>&1; then
    python -m pytest -q "${files[@]}"
    return 0
  fi
  if command -v cmd.exe >/dev/null 2>&1; then
    if cmd.exe /c py -m pytest --version > /dev/null 2>&1; then
      cmd.exe /c py -m pytest -q "${files[@]}"
      return 0
    fi
  fi
  if command -v powershell.exe >/dev/null 2>&1; then
    local targets_file
    targets_file="$(mktemp)"
    printf "%s\n" "${files[@]}" > "${targets_file}"
    if PYTEST_TARGETS_FILE="${targets_file}" powershell.exe -NoProfile -Command '$ErrorActionPreference = "Stop"; $targets = @(); if ($env:PYTEST_TARGETS_FILE -and (Test-Path -LiteralPath $env:PYTEST_TARGETS_FILE)) { $targets = Get-Content -LiteralPath $env:PYTEST_TARGETS_FILE | ForEach-Object { $_.Trim() } | Where-Object { $_ } }; if ($targets.Count -gt 0) { python -m pytest -q @targets } else { python -m pytest -q }'; then
      rm -f "${targets_file}"
      return 0
    fi
    rm -f "${targets_file}"
    return 1
  fi
  echo "pytest unavailable" >&2
  return 1
}

resolve_soak_status() {
  local status="missing"
  if [[ -f "${SOAK_PROGRESS}" ]]; then
    status="$(
      python3 - "${SOAK_PROGRESS}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("invalid")
    raise SystemExit(0)
status = str(payload.get("status") or "").strip().lower()
print(status or "empty")
PY
    )"
  fi
  resolved_soak_status="${status}"
}

resolve_p20_modes() {
  resolve_soak_status
  case "${P20_CHECKLIST_STRICT_MODE}" in
    auto)
      if [[ "${resolved_soak_status}" == "completed" ]]; then
        resolved_checklist_strict="1"
        strict_decision_reason="auto_completed_soak"
      else
        resolved_checklist_strict="0"
        strict_decision_reason="auto_non_completed_soak:${resolved_soak_status}"
      fi
      ;;
    1)
      resolved_checklist_strict="1"
      strict_decision_reason="forced_strict"
      ;;
    0)
      resolved_checklist_strict="0"
      strict_decision_reason="forced_non_strict"
      ;;
    *)
      echo "Invalid P20_CHECKLIST_STRICT_MODE=${P20_CHECKLIST_STRICT_MODE}; expected auto|0|1" >&2
      return 1
      ;;
  esac

  case "${P20_ACCEPTANCE_MODE}" in
    auto)
      if [[ "${resolved_checklist_strict}" == "1" ]]; then
        resolved_acceptance_mode="1"
      else
        resolved_acceptance_mode="0"
      fi
      ;;
    1)
      resolved_acceptance_mode="1"
      ;;
    0)
      resolved_acceptance_mode="0"
      ;;
    *)
      echo "Invalid P20_ACCEPTANCE_MODE=${P20_ACCEPTANCE_MODE}; expected auto|0|1" >&2
      return 1
      ;;
  esac
}

run_stage() {
  local status_var="$1"
  local step_name="$2"
  shift 2
  current_step="${step_name}"
  printf '[p17-p20] %s\n' "${step_name}"
  eval "${status_var}='running'"
  if "$@"; then
    eval "${status_var}='passed'"
  else
    eval "${status_var}='failed'"
    return 1
  fi
}

emit_report() {
  ended_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  MERGED_REPORT_FILE="${REPORT_FILE}" python3 - <<'PY'
import json
import os
from pathlib import Path

report_path = Path(os.environ["MERGED_REPORT_FILE"])
is_failed = os.environ.get("exit_code") != "0"

def load_json(path_text: str):
    path = Path(path_text)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

p20_rc = load_json(os.environ.get("P20_RC_REPORT", ""))
p20_checklist = load_json(os.environ.get("P20_CHECKLIST_REPORT", ""))
p20_manifest = load_json(os.environ.get("P20_RELEASE_MANIFEST", ""))

payload = {
    "started_at": os.environ.get("started_at"),
    "ended_at": os.environ.get("ended_at"),
    "status": "failed" if is_failed else "passed",
    "failed_step": os.environ.get("current_step") if is_failed else None,
    "soak_status": os.environ.get("resolved_soak_status"),
    "strict_decision": {
        "input_mode": os.environ.get("P20_CHECKLIST_STRICT_MODE"),
        "resolved": os.environ.get("resolved_checklist_strict") == "1",
        "reason": os.environ.get("strict_decision_reason"),
    },
    "p20_acceptance_mode": {
        "input_mode": os.environ.get("P20_ACCEPTANCE_MODE"),
        "resolved": os.environ.get("resolved_acceptance_mode") == "1",
    },
    "stage_status": {
        "p17_security": os.environ.get("p17_status"),
        "p18_lighter": os.environ.get("p18_status"),
        "p19_observability": os.environ.get("p19_status"),
        "db_migrate": os.environ.get("db_migrate_status"),
        "release_gate": os.environ.get("release_gate_status"),
        "p20_rc_gate": os.environ.get("p20_rc_status"),
        "p20_release_manifest": os.environ.get("p20_manifest_status"),
    },
    "artifacts": {
        "release_gate_report": os.environ.get("RELEASE_GATE_REPORT"),
        "p16_acceptance_report": os.environ.get("P16_ACCEPTANCE_REPORT"),
        "p20_rc_report": os.environ.get("P20_RC_REPORT"),
        "p20_checklist_report": os.environ.get("P20_CHECKLIST_REPORT"),
        "p20_release_manifest": os.environ.get("P20_RELEASE_MANIFEST"),
    },
    "release_readiness": {
        "checklist_ready_for_release": p20_checklist.get("ready_for_release"),
        "manifest_ready_for_release": p20_manifest.get("ready_for_release"),
        "rc_checklist_status": p20_rc.get("checklist_status"),
        "rc_checklist_strict_mode": p20_rc.get("checklist_strict_mode"),
    },
}

report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False))
PY
}

on_exit() {
  local code="$1"
  exit_code="${code}"
  export ended_at exit_code current_step
  export p17_status p18_status p19_status db_migrate_status release_gate_status p20_rc_status p20_manifest_status
  export resolved_soak_status resolved_checklist_strict resolved_acceptance_mode strict_decision_reason
  export P20_CHECKLIST_STRICT_MODE P20_ACCEPTANCE_MODE
  emit_report
  if [[ "${code}" -ne 0 ]]; then
    echo "P17-P20 merged gate failed at step: ${current_step}" >&2
  else
    echo "P17-P20 merged gate completed. Report: ${REPORT_FILE}"
  fi
}

trap 'on_exit $?' EXIT

export started_at
export RELEASE_GATE_REPORT P16_ACCEPTANCE_REPORT P20_RC_REPORT P20_CHECKLIST_REPORT P20_RELEASE_MANIFEST

if [[ "${RUN_P17_TESTS}" == "1" ]]; then
  run_stage p17_status "p17_security_tests" run_pytest_target \
    tests/test_step_up_matrix.py \
    tests/test_login_anomaly_scoring.py \
    tests/test_rotate_aes_script.py \
    tests/test_tenant_isolation.py
fi

if [[ "${RUN_P18_TESTS}" == "1" ]]; then
  run_stage p18_status "p18_lighter_tests" run_pytest_target \
    tests/test_exchange_accounts_lighter_reconcile.py \
    tests/test_lighter_reconcile_records.py \
    tests/test_lighter_reconcile_maintenance_script.py \
    tests/test_orders_lighter_validation.py
fi

if [[ "${RUN_P19_TESTS}" == "1" ]]; then
  run_stage p19_status "p19_observability_tests" run_pytest_target \
    tests/test_ops_metrics.py \
    tests/test_ops_admin_metrics.py \
    tests/test_ops_metrics_router.py \
    tests/test_ws_event_format.py
fi

if [[ "${RUN_DB_MIGRATE}" == "1" ]]; then
  run_stage db_migrate_status "db_migrate" bash deploy/db_migrate.sh
fi

if [[ "${RUN_RELEASE_GATE}" == "1" ]]; then
  run_stage release_gate_status "release_gate" bash deploy/release_gate.sh
fi

resolve_p20_modes
echo "[p17-p20] p20 strict resolved=${resolved_checklist_strict} (reason=${strict_decision_reason})"
echo "[p17-p20] p20 acceptance mode resolved=${resolved_acceptance_mode}"

if [[ "${RUN_P20_RC}" == "1" ]]; then
  run_stage p20_rc_status "p20_rc_gate" bash -lc \
    "P20_CHECKLIST_STRICT=${resolved_checklist_strict} RUN_RELEASE_GATE=${P20_RUN_RELEASE_GATE} RUN_P16_ACCEPTANCE=${resolved_acceptance_mode} RUN_P20_CHECKLIST=${P20_RUN_CHECKLIST} RELEASE_GATE_REPORT='${RELEASE_GATE_REPORT}' P16_ACCEPTANCE_REPORT='${P16_ACCEPTANCE_REPORT}' REPORT_FILE='${P20_RC_REPORT}' P20_CHECKLIST_REPORT='${P20_CHECKLIST_REPORT}' SOAK_PROGRESS='${SOAK_PROGRESS}' bash deploy/p20_rc_gate.sh"
fi

if [[ "${RUN_P20_MANIFEST}" == "1" ]]; then
  if [[ "${resolved_checklist_strict}" == "1" ]]; then
    run_stage p20_manifest_status "p20_release_manifest_strict" python3 deploy/p20_release_manifest.py \
      --release-gate-report "${RELEASE_GATE_REPORT}" \
      --p16-acceptance-report "${P16_ACCEPTANCE_REPORT}" \
      --p20-rc-report "${P20_RC_REPORT}" \
      --p20-checklist-report "${P20_CHECKLIST_REPORT}" \
      --output "${P20_RELEASE_MANIFEST}" \
      --strict
  else
    run_stage p20_manifest_status "p20_release_manifest" python3 deploy/p20_release_manifest.py \
      --release-gate-report "${RELEASE_GATE_REPORT}" \
      --p16-acceptance-report "${P16_ACCEPTANCE_REPORT}" \
      --p20-rc-report "${P20_RC_REPORT}" \
      --p20-checklist-report "${P20_CHECKLIST_REPORT}" \
      --output "${P20_RELEASE_MANIFEST}"
  fi
fi
