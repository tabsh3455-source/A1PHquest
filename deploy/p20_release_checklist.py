#!/usr/bin/env python3
"""Build a release checklist summary from existing gate reports."""

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


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


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
    parser = argparse.ArgumentParser(description="Generate P20 release checklist.")
    parser.add_argument("--release-gate-report", default="deploy/release_gate_report.json")
    parser.add_argument("--p16-acceptance-report", default="deploy/p16_acceptance_report.json")
    parser.add_argument("--p20-rc-report", default="deploy/p20_rc_report.json")
    parser.add_argument("--soak-progress", default="deploy/p16_soak_progress.json")
    parser.add_argument("--required-evidence-path", default="")
    parser.add_argument("--required-run-id", default="")
    parser.add_argument("--output", default="deploy/p20_release_checklist.json")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero when any required checklist item is not satisfied.",
    )
    args = parser.parse_args()

    release_gate = _load_json(Path(args.release_gate_report))
    p16_acceptance = _load_json(Path(args.p16_acceptance_report))
    p20_rc = _load_json(Path(args.p20_rc_report))
    soak_progress = _load_json(Path(args.soak_progress))
    acceptance_evidence = p16_acceptance.get("evidence") if isinstance(p16_acceptance.get("evidence"), dict) else {}

    release_gate_passed = bool(release_gate.get("status") == "passed")
    acceptance_passed = bool(p16_acceptance.get("accepted") is True)
    p20_release_gate_passed = bool(p20_rc.get("release_gate_passed") is True)
    p20_acceptance_passed = bool(p20_rc.get("acceptance_passed") is True)
    soak_status = str(soak_progress.get("status") or "").strip().lower()
    soak_completed = soak_status == "completed"
    acceptance_evidence_path = str(acceptance_evidence.get("path") or "").strip()
    acceptance_evidence_run_id = str(acceptance_evidence.get("run_id") or "").strip()
    rc_evidence_path = str(p20_rc.get("evidence_path") or "").strip()
    rc_evidence_run_id = str(p20_rc.get("evidence_run_id") or "").strip()
    required_evidence_path = str(args.required_evidence_path or "").strip()
    required_run_id = str(args.required_run_id or "").strip()

    evidence_path_present = bool(acceptance_evidence_path and rc_evidence_path)
    evidence_run_id_present = bool(acceptance_evidence_run_id and rc_evidence_run_id)
    evidence_path_consistent = evidence_path_present and _paths_equal(acceptance_evidence_path, rc_evidence_path)
    evidence_run_id_consistent = evidence_run_id_present and acceptance_evidence_run_id == rc_evidence_run_id
    evidence_path_matches_required = (
        True
        if not required_evidence_path
        else (
            _paths_equal(acceptance_evidence_path, required_evidence_path)
            and _paths_equal(rc_evidence_path, required_evidence_path)
        )
    )
    evidence_run_id_matches_required = (
        True
        if not required_run_id
        else (acceptance_evidence_run_id == required_run_id and rc_evidence_run_id == required_run_id)
    )

    checks = [
        _check(
            "release_gate_passed",
            release_gate_passed,
            f"release_gate.status={release_gate.get('status')}",
        ),
        _check(
            "p16_acceptance_passed",
            acceptance_passed,
            f"p16_acceptance.accepted={p16_acceptance.get('accepted')}",
        ),
        _check(
            "p20_rc_release_gate_passed",
            p20_release_gate_passed,
            f"p20_rc.release_gate_passed={p20_rc.get('release_gate_passed')}",
        ),
        _check(
            "p20_rc_acceptance_passed",
            p20_acceptance_passed,
            f"p20_rc.acceptance_passed={p20_rc.get('acceptance_passed')}",
        ),
        _check(
            "p16_soak_completed",
            soak_completed,
            f"p16_soak_progress.status={soak_status or 'missing'}",
        ),
        _check(
            "evidence_path_present",
            evidence_path_present,
            (
                "acceptance.evidence.path and p20_rc.evidence_path must both be present; "
                f"acceptance={acceptance_evidence_path or 'missing'}, rc={rc_evidence_path or 'missing'}"
            ),
        ),
        _check(
            "evidence_run_id_present",
            evidence_run_id_present,
            (
                "acceptance.evidence.run_id and p20_rc.evidence_run_id must both be present; "
                f"acceptance={acceptance_evidence_run_id or 'missing'}, rc={rc_evidence_run_id or 'missing'}"
            ),
        ),
        _check(
            "evidence_path_consistent",
            evidence_path_consistent,
            f"acceptance.evidence.path={acceptance_evidence_path or 'missing'}, p20_rc.evidence_path={rc_evidence_path or 'missing'}",
        ),
        _check(
            "evidence_run_id_consistent",
            evidence_run_id_consistent,
            (
                "acceptance.evidence.run_id="
                f"{acceptance_evidence_run_id or 'missing'}, p20_rc.evidence_run_id={rc_evidence_run_id or 'missing'}"
            ),
        ),
        _check(
            "evidence_path_matches_required",
            evidence_path_matches_required,
            (
                f"required_evidence_path={required_evidence_path or 'not_set'}, "
                f"acceptance={acceptance_evidence_path or 'missing'}, rc={rc_evidence_path or 'missing'}"
            ),
        ),
        _check(
            "evidence_run_id_matches_required",
            evidence_run_id_matches_required,
            (
                f"required_run_id={required_run_id or 'not_set'}, "
                f"acceptance={acceptance_evidence_run_id or 'missing'}, rc={rc_evidence_run_id or 'missing'}"
            ),
        ),
    ]
    ready_for_release = all(bool(item.get("ok")) for item in checks)

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "ready_for_release": ready_for_release,
        "checks": checks,
        "evidence_binding": {
            "acceptance_evidence_path": acceptance_evidence_path,
            "acceptance_evidence_run_id": acceptance_evidence_run_id,
            "rc_evidence_path": rc_evidence_path,
            "rc_evidence_run_id": rc_evidence_run_id,
            "required_evidence_path": required_evidence_path or None,
            "required_run_id": required_run_id or None,
        },
        "inputs": {
            "release_gate_report": args.release_gate_report,
            "p16_acceptance_report": args.p16_acceptance_report,
            "p20_rc_report": args.p20_rc_report,
            "soak_progress": args.soak_progress,
        },
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))

    if args.strict and not ready_for_release:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
