from pathlib import Path
import json
import shutil
import shlex
import subprocess

import pytest


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _to_wsl_path(path: Path) -> str:
    raw = str(path.resolve())
    if len(raw) >= 2 and raw[1] == ":":
        drive = raw[0].lower()
        rest = raw[2:].replace("\\", "/")
        return f"/mnt/{drive}{rest}"
    return raw.replace("\\", "/")


def _run_gate_with_inline_env(*, env_pairs: dict[str, str]) -> subprocess.CompletedProcess[str]:
    parts = [f"{key}={shlex.quote(value)}" for key, value in env_pairs.items()]
    cmd = " ".join(parts + ["bash", "deploy/p20_rc_gate.sh"])
    cwd = shlex.quote(_to_wsl_path(Path.cwd()))
    return subprocess.run(
        ["bash", "-lc", f"cd {cwd} && {cmd}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required for gate script tests")
def test_p20_rc_gate_runs_checklist_in_non_strict_mode(tmp_path: Path):
    release_gate_report = tmp_path / "release_gate_report.json"
    p16_acceptance_report = tmp_path / "p16_acceptance_report.json"
    soak_progress = tmp_path / "p16_soak_progress.json"
    report_file = tmp_path / "p20_rc_report.json"
    checklist_file = tmp_path / "p20_release_checklist.json"

    _write_json(release_gate_report, {"status": "passed"})
    _write_json(p16_acceptance_report, {"accepted": True})
    _write_json(soak_progress, {"status": "completed"})

    env_pairs = {
        "RUN_RELEASE_GATE": "0",
        "RUN_P16_ACCEPTANCE": "0",
        "RUN_P20_CHECKLIST": "1",
        "P20_CHECKLIST_STRICT": "0",
        "RELEASE_GATE_REPORT": _to_wsl_path(release_gate_report),
        "P16_ACCEPTANCE_REPORT": _to_wsl_path(p16_acceptance_report),
        "SOAK_PROGRESS": _to_wsl_path(soak_progress),
        "REPORT_FILE": _to_wsl_path(report_file),
        "P20_CHECKLIST_REPORT": _to_wsl_path(checklist_file),
    }
    result = _run_gate_with_inline_env(env_pairs=env_pairs)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "P20 RC gate completed." in result.stdout

    payload = json.loads(report_file.read_text(encoding="utf-8"))
    assert payload["checklist_status"] == "passed"
    assert payload["checklist_strict_mode"] is False
    assert payload["checklist_report_path"] == _to_wsl_path(checklist_file)
    # Acceptance was intentionally skipped in this test, so release readiness stays false.
    assert payload["checklist_ready_for_release"] is False


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required for gate script tests")
def test_p20_rc_gate_fails_when_checklist_strict_not_ready(tmp_path: Path):
    release_gate_report = tmp_path / "release_gate_report.json"
    p16_acceptance_report = tmp_path / "p16_acceptance_report.json"
    soak_progress = tmp_path / "p16_soak_progress.json"
    report_file = tmp_path / "p20_rc_report.json"
    checklist_file = tmp_path / "p20_release_checklist.json"

    _write_json(release_gate_report, {"status": "passed"})
    _write_json(p16_acceptance_report, {"accepted": True})
    _write_json(soak_progress, {"status": "completed"})

    env_pairs = {
        "RUN_RELEASE_GATE": "0",
        "RUN_P16_ACCEPTANCE": "0",
        "RUN_P20_CHECKLIST": "1",
        "P20_CHECKLIST_STRICT": "1",
        "RELEASE_GATE_REPORT": _to_wsl_path(release_gate_report),
        "P16_ACCEPTANCE_REPORT": _to_wsl_path(p16_acceptance_report),
        "SOAK_PROGRESS": _to_wsl_path(soak_progress),
        "REPORT_FILE": _to_wsl_path(report_file),
        "P20_CHECKLIST_REPORT": _to_wsl_path(checklist_file),
    }
    result = _run_gate_with_inline_env(env_pairs=env_pairs)
    assert result.returncode == 1
    assert "checklist strict mode failed" in result.stdout.lower()
    payload = json.loads(report_file.read_text(encoding="utf-8"))
    assert payload["checklist_status"] == "failed"
    assert payload["checklist_strict_mode"] is True
    assert payload["checklist_ready_for_release"] is False
