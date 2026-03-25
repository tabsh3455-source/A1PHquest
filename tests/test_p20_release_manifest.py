from pathlib import Path
import json
import subprocess
import sys


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_p20_release_manifest_ready_when_consistent(tmp_path: Path):
    release_gate = tmp_path / "release_gate_report.json"
    acceptance = tmp_path / "p16_acceptance_report.json"
    rc_report = tmp_path / "p20_rc_report.json"
    checklist = tmp_path / "p20_release_checklist.json"
    output = tmp_path / "p20_release_manifest.json"

    _write_json(release_gate, {"status": "passed", "checked_at": "2026-03-24T00:00:00Z"})
    _write_json(
        acceptance,
        {
            "accepted": True,
            "checked_at": "2026-03-24T00:01:00Z",
            "evidence": {"path": "deploy/evidence/p16/run-a", "run_id": "run-a"},
        },
    )
    _write_json(
        rc_report,
        {
            "release_gate_passed": True,
            "acceptance_passed": True,
            "checklist_status": "passed",
            "checklist_ready_for_release": True,
            "evidence_path": "deploy/evidence/p16/run-a",
            "evidence_run_id": "run-a",
        },
    )
    _write_json(checklist, {"ready_for_release": True, "checked_at": "2026-03-24T00:02:00Z"})

    result = subprocess.run(
        [
            sys.executable,
            "deploy/p20_release_manifest.py",
            "--release-gate-report",
            str(release_gate),
            "--p16-acceptance-report",
            str(acceptance),
            "--p20-rc-report",
            str(rc_report),
            "--p20-checklist-report",
            str(checklist),
            "--output",
            str(output),
            "--strict",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout.strip())
    assert payload["ready_for_release"] is True
    assert payload["evidence_binding"]["path_consistent"] is True
    assert payload["evidence_binding"]["run_id_consistent"] is True
    assert output.exists()


def test_p20_release_manifest_strict_fails_when_evidence_mismatch(tmp_path: Path):
    release_gate = tmp_path / "release_gate_report.json"
    acceptance = tmp_path / "p16_acceptance_report.json"
    rc_report = tmp_path / "p20_rc_report.json"
    checklist = tmp_path / "p20_release_checklist.json"
    output = tmp_path / "p20_release_manifest.json"

    _write_json(release_gate, {"status": "passed"})
    _write_json(
        acceptance,
        {
            "accepted": True,
            "evidence": {"path": "deploy/evidence/p16/run-a", "run_id": "run-a"},
        },
    )
    _write_json(
        rc_report,
        {
            "release_gate_passed": True,
            "acceptance_passed": True,
            "evidence_path": "deploy/evidence/p16/run-b",
            "evidence_run_id": "run-b",
        },
    )
    _write_json(checklist, {"ready_for_release": True})

    result = subprocess.run(
        [
            sys.executable,
            "deploy/p20_release_manifest.py",
            "--release-gate-report",
            str(release_gate),
            "--p16-acceptance-report",
            str(acceptance),
            "--p20-rc-report",
            str(rc_report),
            "--p20-checklist-report",
            str(checklist),
            "--output",
            str(output),
            "--strict",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout.strip())
    assert payload["ready_for_release"] is False
    assert payload["evidence_binding"]["path_consistent"] is False
