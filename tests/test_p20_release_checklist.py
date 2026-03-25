from pathlib import Path
import json
import subprocess
import sys


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_p20_release_checklist_ready_when_all_checks_pass(tmp_path):
    release_gate = tmp_path / "release_gate_report.json"
    p16_acceptance = tmp_path / "p16_acceptance_report.json"
    p20_rc = tmp_path / "p20_rc_report.json"
    soak_progress = tmp_path / "p16_soak_progress.json"
    output = tmp_path / "p20_release_checklist.json"

    _write_json(release_gate, {"status": "passed"})
    _write_json(
        p16_acceptance,
        {
            "accepted": True,
            "evidence": {
                "path": "deploy/evidence/p16/run-a",
                "run_id": "run-a",
            },
        },
    )
    _write_json(
        p20_rc,
        {
            "release_gate_passed": True,
            "acceptance_passed": True,
            "evidence_path": "deploy/evidence/p16/run-a",
            "evidence_run_id": "run-a",
        },
    )
    _write_json(soak_progress, {"status": "completed"})

    result = subprocess.run(
        [
            sys.executable,
            "deploy/p20_release_checklist.py",
            "--release-gate-report",
            str(release_gate),
            "--p16-acceptance-report",
            str(p16_acceptance),
            "--p20-rc-report",
            str(p20_rc),
            "--soak-progress",
            str(soak_progress),
            "--required-evidence-path",
            "deploy/evidence/p16/run-a",
            "--required-run-id",
            "run-a",
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
    assert all(item["ok"] for item in payload["checks"])
    assert output.exists()


def test_p20_release_checklist_strict_fails_when_soak_not_completed(tmp_path):
    release_gate = tmp_path / "release_gate_report.json"
    p16_acceptance = tmp_path / "p16_acceptance_report.json"
    p20_rc = tmp_path / "p20_rc_report.json"
    soak_progress = tmp_path / "p16_soak_progress.json"
    output = tmp_path / "p20_release_checklist.json"

    _write_json(release_gate, {"status": "passed"})
    _write_json(
        p16_acceptance,
        {
            "accepted": True,
            "evidence": {
                "path": "deploy/evidence/p16/run-a",
                "run_id": "run-a",
            },
        },
    )
    _write_json(
        p20_rc,
        {
            "release_gate_passed": True,
            "acceptance_passed": True,
            "evidence_path": "deploy/evidence/p16/run-a",
            "evidence_run_id": "run-a",
        },
    )
    _write_json(soak_progress, {"status": "running"})

    result = subprocess.run(
        [
            sys.executable,
            "deploy/p20_release_checklist.py",
            "--release-gate-report",
            str(release_gate),
            "--p16-acceptance-report",
            str(p16_acceptance),
            "--p20-rc-report",
            str(p20_rc),
            "--soak-progress",
            str(soak_progress),
            "--required-evidence-path",
            "deploy/evidence/p16/run-a",
            "--required-run-id",
            "run-a",
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
    assert any(item["name"] == "p16_soak_completed" and item["ok"] is False for item in payload["checks"])


def test_p20_release_checklist_strict_fails_when_required_evidence_mismatch(tmp_path):
    release_gate = tmp_path / "release_gate_report.json"
    p16_acceptance = tmp_path / "p16_acceptance_report.json"
    p20_rc = tmp_path / "p20_rc_report.json"
    soak_progress = tmp_path / "p16_soak_progress.json"
    output = tmp_path / "p20_release_checklist.json"

    _write_json(release_gate, {"status": "passed"})
    _write_json(
        p16_acceptance,
        {
            "accepted": True,
            "evidence": {
                "path": "deploy/evidence/p16/run-a",
                "run_id": "run-a",
            },
        },
    )
    _write_json(
        p20_rc,
        {
            "release_gate_passed": True,
            "acceptance_passed": True,
            "evidence_path": "deploy/evidence/p16/run-a",
            "evidence_run_id": "run-a",
        },
    )
    _write_json(soak_progress, {"status": "completed"})

    result = subprocess.run(
        [
            sys.executable,
            "deploy/p20_release_checklist.py",
            "--release-gate-report",
            str(release_gate),
            "--p16-acceptance-report",
            str(p16_acceptance),
            "--p20-rc-report",
            str(p20_rc),
            "--soak-progress",
            str(soak_progress),
            "--required-evidence-path",
            "deploy/evidence/p16/run-b",
            "--required-run-id",
            "run-b",
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
    assert any(item["name"] == "evidence_path_matches_required" and item["ok"] is False for item in payload["checks"])
