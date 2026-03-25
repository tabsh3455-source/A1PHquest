#!/usr/bin/env python3
"""Aggregate P20 release evidence into a single manifest JSON."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _normalize_path(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = str(Path(text)).replace("\\", "/").rstrip("/")
    if len(normalized) >= 2 and normalized[1] == ":":
        normalized = normalized[0].lower() + normalized[1:]
    return normalized


def _paths_equal(left: str, right: str) -> bool:
    return _normalize_path(left) == _normalize_path(right)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate unified P20 release manifest.")
    parser.add_argument("--release-gate-report", default="deploy/release_gate_report.json")
    parser.add_argument("--p16-acceptance-report", default="deploy/p16_acceptance_report.json")
    parser.add_argument("--p20-rc-report", default="deploy/p20_rc_report.json")
    parser.add_argument("--p20-checklist-report", default="deploy/p20_release_checklist.json")
    parser.add_argument("--output", default="deploy/p20_release_manifest.json")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when ready_for_release is false.")
    args = parser.parse_args()

    release_gate_path = Path(args.release_gate_report)
    acceptance_path = Path(args.p16_acceptance_report)
    rc_path = Path(args.p20_rc_report)
    checklist_path = Path(args.p20_checklist_report)

    release_gate = _load_json(release_gate_path)
    acceptance = _load_json(acceptance_path)
    rc_report = _load_json(rc_path)
    checklist = _load_json(checklist_path)

    acceptance_evidence = acceptance.get("evidence") if isinstance(acceptance.get("evidence"), dict) else {}
    acceptance_evidence_path = str(acceptance_evidence.get("path") or "").strip()
    acceptance_run_id = str(acceptance_evidence.get("run_id") or "").strip()
    rc_evidence_path = str(rc_report.get("evidence_path") or "").strip()
    rc_run_id = str(rc_report.get("evidence_run_id") or "").strip()

    evidence_path_consistent = bool(
        acceptance_evidence_path and rc_evidence_path and _paths_equal(acceptance_evidence_path, rc_evidence_path)
    )
    evidence_run_id_consistent = bool(acceptance_run_id and rc_run_id and acceptance_run_id == rc_run_id)
    checklist_ready = bool(checklist.get("ready_for_release") is True)
    ready_for_release = checklist_ready and evidence_path_consistent and evidence_run_id_consistent

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ready_for_release": ready_for_release,
        "release_gate": {
            "report_path": str(release_gate_path),
            "status": release_gate.get("status"),
            "checked_at": release_gate.get("checked_at"),
        },
        "acceptance": {
            "report_path": str(acceptance_path),
            "accepted": acceptance.get("accepted"),
            "checked_at": acceptance.get("checked_at"),
            "evidence_path": acceptance_evidence_path or None,
            "evidence_run_id": acceptance_run_id or None,
        },
        "rc": {
            "report_path": str(rc_path),
            "checked_at": rc_report.get("checked_at"),
            "release_gate_passed": rc_report.get("release_gate_passed"),
            "acceptance_passed": rc_report.get("acceptance_passed"),
            "checklist_status": rc_report.get("checklist_status"),
            "checklist_ready_for_release": rc_report.get("checklist_ready_for_release"),
            "soak_progress_path": rc_report.get("soak_progress_path"),
            "evidence_path": rc_evidence_path or None,
            "evidence_run_id": rc_run_id or None,
        },
        "checklist": {
            "report_path": str(checklist_path),
            "checked_at": checklist.get("checked_at"),
            "ready_for_release": checklist.get("ready_for_release"),
        },
        "evidence_binding": {
            "path_consistent": evidence_path_consistent,
            "run_id_consistent": evidence_run_id_consistent,
            "acceptance_evidence_path": acceptance_evidence_path or None,
            "rc_evidence_path": rc_evidence_path or None,
            "acceptance_run_id": acceptance_run_id or None,
            "rc_run_id": rc_run_id or None,
        },
        "strict_release_command": "python3 deploy/p20_release_checklist.py --strict",
        "release_rule": (
            "Only proceed to production when strict checklist exits 0. "
            "strict=0 is rehearsal-only and cannot enter production window."
        ),
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))

    if args.strict and not ready_for_release:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

