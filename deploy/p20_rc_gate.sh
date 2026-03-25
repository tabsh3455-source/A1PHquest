#!/usr/bin/env bash
set -euo pipefail

# P20 release-candidate gate:
# - executes existing release_gate pipeline
# - validates latest full-soak evidence bundle and emits acceptance report

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

REPORT_FILE="${REPORT_FILE:-deploy/p20_rc_report.json}"
RUN_RELEASE_GATE="${RUN_RELEASE_GATE:-1}"
RUN_P16_ACCEPTANCE="${RUN_P16_ACCEPTANCE:-1}"
RUN_P20_CHECKLIST="${RUN_P20_CHECKLIST:-1}"
P20_CHECKLIST_STRICT="${P20_CHECKLIST_STRICT:-0}"
RELEASE_GATE_REPORT="${RELEASE_GATE_REPORT:-deploy/release_gate_report.json}"
P16_ACCEPTANCE_REPORT="${P16_ACCEPTANCE_REPORT:-deploy/p16_acceptance_report.json}"
P20_CHECKLIST_REPORT="${P20_CHECKLIST_REPORT:-deploy/p20_release_checklist.json}"
EVIDENCE_DIR="${EVIDENCE_DIR:-deploy/evidence/p16}"
EVIDENCE_PATH="${EVIDENCE_PATH:-}"
SOAK_PROGRESS="${SOAK_PROGRESS:-deploy/p16_soak_progress.json}"
SOAK_REPORT="${SOAK_REPORT:-}"
if [[ -z "${SOAK_REPORT}" ]]; then
  if [[ -n "${EVIDENCE_PATH}" && -f "${EVIDENCE_PATH}/p16_soak_report.json" ]]; then
    # Prefer report bundled with selected evidence run to avoid checksum mismatch.
    SOAK_REPORT="${EVIDENCE_PATH}/p16_soak_report.json"
  else
    SOAK_REPORT="deploy/p16_soak_report.json"
  fi
fi

release_gate_status="skipped"
acceptance_status="skipped"
checklist_status="skipped"
checklist_strict_failed=0

if [[ "${RUN_RELEASE_GATE}" == "1" ]]; then
  bash deploy/release_gate.sh
  release_gate_status="passed"
fi

if [[ "${RUN_P16_ACCEPTANCE}" == "1" ]]; then
  acceptance_cmd=(
    python3 deploy/p16_acceptance_report.py
    --soak-report "${SOAK_REPORT}"
    --output "${P16_ACCEPTANCE_REPORT}"
  )
  if [[ -n "${EVIDENCE_PATH}" ]]; then
    acceptance_cmd+=(--evidence-path "${EVIDENCE_PATH}")
  else
    acceptance_cmd+=(--evidence-dir "${EVIDENCE_DIR}")
  fi
  "${acceptance_cmd[@]}"
  acceptance_status="passed"
fi

export release_gate_status
export acceptance_status
export RELEASE_GATE_REPORT
export P16_ACCEPTANCE_REPORT

python3 - <<'PY' > "${REPORT_FILE}"
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def load_json(path: str) -> dict:
    file = Path(path)
    if not file.exists():
        return {}
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except Exception:
        return {}


release_gate_report = load_json(os.environ.get("RELEASE_GATE_REPORT", "deploy/release_gate_report.json"))
acceptance_report = load_json(os.environ.get("P16_ACCEPTANCE_REPORT", "deploy/p16_acceptance_report.json"))
acceptance_evidence = acceptance_report.get("evidence") if isinstance(acceptance_report.get("evidence"), dict) else {}
report = {
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "release_gate_status": os.environ.get("release_gate_status", "skipped"),
    "acceptance_status": os.environ.get("acceptance_status", "skipped"),
    "release_gate_report_path": os.environ.get("RELEASE_GATE_REPORT"),
    "acceptance_report_path": os.environ.get("P16_ACCEPTANCE_REPORT"),
    "release_gate_passed": bool(release_gate_report.get("status") == "passed"),
    "acceptance_passed": bool(acceptance_report.get("accepted", False))
    if os.environ.get("acceptance_status", "skipped") == "passed"
    else None,
    "evidence_path": acceptance_evidence.get("path"),
    "evidence_run_id": acceptance_evidence.get("run_id"),
    "evidence_metadata_path": acceptance_evidence.get("metadata_path"),
}
print(json.dumps(report, ensure_ascii=False))
PY

if [[ "${RUN_P20_CHECKLIST}" == "1" ]]; then
  checklist_cmd=(
    python3 deploy/p20_release_checklist.py
    --release-gate-report "${RELEASE_GATE_REPORT}"
    --p16-acceptance-report "${P16_ACCEPTANCE_REPORT}"
    --p20-rc-report "${REPORT_FILE}"
    --soak-progress "${SOAK_PROGRESS}"
    --output "${P20_CHECKLIST_REPORT}"
  )
  if [[ "${P20_CHECKLIST_STRICT}" == "1" ]]; then
    checklist_cmd+=(--strict)
  fi
  if "${checklist_cmd[@]}"; then
    checklist_status="passed"
  else
    checklist_status="failed"
    if [[ "${P20_CHECKLIST_STRICT}" == "1" ]]; then
      checklist_strict_failed=1
    fi
  fi
fi

export REPORT_FILE
export checklist_status
export P20_CHECKLIST_REPORT
export P20_CHECKLIST_STRICT
export SOAK_PROGRESS
python3 - <<'PY'
import json
import os
from pathlib import Path


def load_json(path: str) -> dict:
    file = Path(path)
    if not file.exists():
        return {}
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except Exception:
        return {}


report_file = Path(os.environ.get("REPORT_FILE", "deploy/p20_rc_report.json"))
report = load_json(str(report_file))
checklist_file = os.environ.get("P20_CHECKLIST_REPORT", "deploy/p20_release_checklist.json")
checklist = load_json(checklist_file)
report["checklist_status"] = os.environ.get("checklist_status", "skipped")
report["checklist_report_path"] = checklist_file
report["checklist_strict_mode"] = os.environ.get("P20_CHECKLIST_STRICT", "0") == "1"
report["checklist_ready_for_release"] = checklist.get("ready_for_release") if checklist else None
report["soak_progress_path"] = os.environ.get("SOAK_PROGRESS", "deploy/p16_soak_progress.json")
report_file.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
PY

echo "P20 RC gate completed. Report: ${REPORT_FILE}"
if [[ "${checklist_strict_failed}" == "1" ]]; then
  echo "P20 RC gate checklist strict mode failed."
  exit 1
fi
