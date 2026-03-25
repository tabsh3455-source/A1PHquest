import hashlib
import json
import subprocess
import sys
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _run_finalize(
    *,
    progress_file: Path,
    evidence_dir: Path,
    acceptance_report: Path,
    output: Path,
    run_p20_rc: int = 0,
) -> subprocess.CompletedProcess[str]:
    script = Path("deploy/p16_finalize_after_soak.py")
    return subprocess.run(
        [
            sys.executable,
            str(script),
            "--progress-file",
            str(progress_file),
            "--evidence-dir",
            str(evidence_dir),
            "--acceptance-report",
            str(acceptance_report),
            "--output",
            str(output),
            "--run-p20-rc",
            str(run_p20_rc),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_finalize_after_soak_returns_running_reason(tmp_path: Path):
    progress = tmp_path / "p16_soak_progress.json"
    progress.write_text(json.dumps({"status": "running"}), encoding="utf-8")
    output = tmp_path / "finalize_report.json"
    result = _run_finalize(
        progress_file=progress,
        evidence_dir=tmp_path / "evidence",
        acceptance_report=tmp_path / "acceptance.json",
        output=output,
        run_p20_rc=0,
    )
    assert result.returncode == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["reason"] == "soak_still_running"


def test_finalize_after_soak_generates_acceptance_report(tmp_path: Path):
    progress = tmp_path / "p16_soak_progress.json"
    progress.write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    evidence_run = tmp_path / "evidence" / "run-a"
    evidence_run.mkdir(parents=True)
    soak_report = evidence_run / "p16_soak_report.json"
    health_report = evidence_run / "p16_soak_health.json"
    stats_log = evidence_run / "p16_soak_stats.log"
    soak_report.write_text(
        json.dumps(
            {
                "checked_at": "2026-03-23T00:00:00Z",
                "passed": True,
                "health_summary": {
                    "all_healthy": True,
                    "totals": {"http://127.0.0.1:8000/healthz": 4},
                    "failures": {"http://127.0.0.1:8000/healthz": 0},
                },
                "sample_totals": {"docker_error_rate": 0.0},
                "checks": {"health_all_healthy": {"passed": True}},
                "peaks": {"peak_total_mem_mib": 100.0},
            }
        ),
        encoding="utf-8",
    )
    health_report.write_text('{"all_healthy":true}', encoding="utf-8")
    stats_log.write_text("2026-03-23T00:00:00Z|a1phquest-api|0.1%|12MiB / 1GiB|4\n", encoding="utf-8")

    metadata = {
        "run_id": "run-a",
        "archived_at": "2026-03-23T00:00:10Z",
        "files": [
            {"name": "soak_report", "destination": str(soak_report), "sha256": _sha256(soak_report)},
            {"name": "health_report", "destination": str(health_report), "sha256": _sha256(health_report)},
            {"name": "stats_log", "destination": str(stats_log), "sha256": _sha256(stats_log)},
        ],
    }
    (evidence_run / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    acceptance_report = tmp_path / "acceptance.json"
    output = tmp_path / "finalize_report.json"
    result = _run_finalize(
        progress_file=progress,
        evidence_dir=tmp_path / "evidence",
        acceptance_report=acceptance_report,
        output=output,
        run_p20_rc=0,
    )
    assert result.returncode == 0, result.stderr
    finalize_payload = json.loads(output.read_text(encoding="utf-8"))
    assert finalize_payload["ok"] is True
    assert finalize_payload["acceptance_passed"] is True

    acceptance_payload = json.loads(acceptance_report.read_text(encoding="utf-8"))
    assert acceptance_payload["accepted"] is True
