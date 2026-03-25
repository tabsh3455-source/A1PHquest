from pathlib import Path
import json
import shlex
import shutil
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


def _run_merged_gate(*, env_pairs: dict[str, str]) -> subprocess.CompletedProcess[str]:
    parts = [f"{key}={shlex.quote(value)}" for key, value in env_pairs.items()]
    cmd = " ".join(parts + ["bash", "deploy/p17_p20_merged_gate.sh"])
    cwd = shlex.quote(_to_wsl_path(Path.cwd()))
    return subprocess.run(
        ["bash", "-lc", f"cd {cwd} && {cmd}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required for merged gate script tests")
def test_merged_gate_auto_non_strict_when_soak_running(tmp_path: Path):
    report_file = tmp_path / "p17_p20_report.json"
    release_gate_report = tmp_path / "release_gate_report.json"
    acceptance_report = tmp_path / "p16_acceptance_report.json"
    soak_progress = tmp_path / "p16_soak_progress.json"
    p20_rc_report = tmp_path / "p20_rc_report.json"
    p20_checklist_report = tmp_path / "p20_release_checklist.json"
    p20_manifest = tmp_path / "p20_release_manifest.json"

    _write_json(release_gate_report, {"status": "passed"})
    _write_json(acceptance_report, {"accepted": False})
    _write_json(soak_progress, {"status": "running"})

    env_pairs = {
        "REPORT_FILE": _to_wsl_path(report_file),
        "RELEASE_GATE_REPORT": _to_wsl_path(release_gate_report),
        "P16_ACCEPTANCE_REPORT": _to_wsl_path(acceptance_report),
        "SOAK_PROGRESS": _to_wsl_path(soak_progress),
        "P20_RC_REPORT": _to_wsl_path(p20_rc_report),
        "P20_CHECKLIST_REPORT": _to_wsl_path(p20_checklist_report),
        "P20_RELEASE_MANIFEST": _to_wsl_path(p20_manifest),
        "RUN_P17_TESTS": "0",
        "RUN_P18_TESTS": "0",
        "RUN_P19_TESTS": "0",
        "RUN_DB_MIGRATE": "0",
        "RUN_RELEASE_GATE": "0",
        "RUN_P20_RC": "1",
        "RUN_P20_MANIFEST": "0",
        "P20_CHECKLIST_STRICT_MODE": "auto",
        "P20_ACCEPTANCE_MODE": "auto",
        "P20_RUN_RELEASE_GATE": "0",
        "P20_RUN_CHECKLIST": "1",
    }
    result = _run_merged_gate(env_pairs=env_pairs)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(report_file.read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert payload["strict_decision"]["resolved"] is False
    assert payload["strict_decision"]["reason"].startswith("auto_non_completed_soak")
    assert payload["stage_status"]["p20_rc_gate"] == "passed"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required for merged gate script tests")
def test_merged_gate_auto_strict_when_soak_completed(tmp_path: Path):
    report_file = tmp_path / "p17_p20_report.json"
    soak_progress = tmp_path / "p16_soak_progress.json"
    _write_json(soak_progress, {"status": "completed"})

    env_pairs = {
        "REPORT_FILE": _to_wsl_path(report_file),
        "SOAK_PROGRESS": _to_wsl_path(soak_progress),
        "RUN_P17_TESTS": "0",
        "RUN_P18_TESTS": "0",
        "RUN_P19_TESTS": "0",
        "RUN_DB_MIGRATE": "0",
        "RUN_RELEASE_GATE": "0",
        "RUN_P20_RC": "0",
        "RUN_P20_MANIFEST": "0",
        "P20_CHECKLIST_STRICT_MODE": "auto",
        "P20_ACCEPTANCE_MODE": "auto",
    }
    result = _run_merged_gate(env_pairs=env_pairs)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(report_file.read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert payload["soak_status"] == "completed"
    assert payload["strict_decision"]["resolved"] is True
    assert payload["strict_decision"]["reason"] == "auto_completed_soak"
    assert payload["stage_status"]["p20_rc_gate"] == "skipped"
