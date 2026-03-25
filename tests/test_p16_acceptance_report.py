import hashlib
import json
import subprocess
import sys
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _run_acceptance(
    *,
    soak_report: Path,
    evidence_path: Path,
    output: Path,
) -> subprocess.CompletedProcess[str]:
    script = Path("deploy/p16_acceptance_report.py")
    return subprocess.run(
        [
            sys.executable,
            str(script),
            "--soak-report",
            str(soak_report),
            "--evidence-path",
            str(evidence_path),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_p16_acceptance_report_passes_when_soak_and_checksums_are_valid(tmp_path: Path):
    soak_report = tmp_path / "p16_soak_report.json"
    soak_report.write_text(
        json.dumps(
            {
                "checked_at": "2026-03-23T00:00:00Z",
                "passed": True,
                "health_summary": {
                    "all_healthy": True,
                    "totals": {"http://127.0.0.1:8000/healthz": 10},
                    "failures": {"http://127.0.0.1:8000/healthz": 0},
                },
                "sample_totals": {"docker_error_rate": 0.0},
                "checks": {
                    "health_all_healthy": {"passed": True},
                    "required_service:a1phquest-api": {"passed": True},
                },
                "peaks": {"peak_total_mem_mib": 128.5},
            }
        ),
        encoding="utf-8",
    )

    evidence_run = tmp_path / "evidence" / "run-1"
    evidence_run.mkdir(parents=True)
    copied_soak = evidence_run / "p16_soak_report.json"
    copied_health = evidence_run / "p16_soak_health.json"
    copied_stats = evidence_run / "p16_soak_stats.log"
    copied_soak.write_text(soak_report.read_text(encoding="utf-8"), encoding="utf-8")
    copied_health.write_text('{"all_healthy":true}', encoding="utf-8")
    copied_stats.write_text("2026-03-23T00:00:00Z|a1phquest-api|0.1%|12MiB / 1GiB|4\n", encoding="utf-8")

    metadata = {
        "run_id": "run-1",
        "archived_at": "2026-03-23T00:00:10Z",
        "files": [
            {"name": "soak_report", "destination": str(copied_soak), "sha256": _sha256(copied_soak)},
            {"name": "health_report", "destination": str(copied_health), "sha256": _sha256(copied_health)},
            {"name": "stats_log", "destination": str(copied_stats), "sha256": _sha256(copied_stats)},
        ],
    }
    (evidence_run / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    output = tmp_path / "acceptance.json"
    result = _run_acceptance(soak_report=soak_report, evidence_path=evidence_run, output=output)
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["accepted"] is True
    assert payload["acceptance_gate"]["checksum_verified"] is True


def test_p16_acceptance_report_fails_on_checksum_mismatch(tmp_path: Path):
    soak_report = tmp_path / "p16_soak_report.json"
    soak_report.write_text(
        json.dumps(
            {
                "checked_at": "2026-03-23T00:00:00Z",
                "passed": True,
                "health_summary": {
                    "all_healthy": True,
                    "totals": {"http://127.0.0.1:8000/healthz": 10},
                    "failures": {"http://127.0.0.1:8000/healthz": 0},
                },
                "sample_totals": {"docker_error_rate": 0.0},
                "checks": {"health_all_healthy": {"passed": True}},
            }
        ),
        encoding="utf-8",
    )

    evidence_run = tmp_path / "evidence" / "run-2"
    evidence_run.mkdir(parents=True)
    copied_soak = evidence_run / "p16_soak_report.json"
    copied_health = evidence_run / "p16_soak_health.json"
    copied_stats = evidence_run / "p16_soak_stats.log"
    copied_soak.write_text(soak_report.read_text(encoding="utf-8"), encoding="utf-8")
    copied_health.write_text('{"all_healthy":true}', encoding="utf-8")
    copied_stats.write_text("sample\n", encoding="utf-8")

    metadata = {
        "run_id": "run-2",
        "archived_at": "2026-03-23T00:00:10Z",
        "files": [
            {"name": "soak_report", "destination": str(copied_soak), "sha256": "deadbeef"},
            {"name": "health_report", "destination": str(copied_health), "sha256": _sha256(copied_health)},
            {"name": "stats_log", "destination": str(copied_stats), "sha256": _sha256(copied_stats)},
        ],
    }
    (evidence_run / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    output = tmp_path / "acceptance.json"
    result = _run_acceptance(soak_report=soak_report, evidence_path=evidence_run, output=output)
    assert result.returncode == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["accepted"] is False
    assert payload["acceptance_gate"]["checksum_verified"] is False
    assert payload["errors"]["checksum_mismatches"]


def test_p16_acceptance_report_fails_when_soak_report_does_not_match_evidence(tmp_path: Path):
    soak_report = tmp_path / "p16_soak_report.json"
    soak_report.write_text(
        json.dumps(
            {
                "checked_at": "2026-03-23T00:00:00Z",
                "passed": True,
                "health_summary": {"all_healthy": True, "totals": {}, "failures": {}},
                "sample_totals": {"docker_error_rate": 0.0},
                "checks": {"health_all_healthy": {"passed": True}},
            }
        ),
        encoding="utf-8",
    )

    evidence_run = tmp_path / "evidence" / "run-3"
    evidence_run.mkdir(parents=True)
    copied_soak = evidence_run / "p16_soak_report.json"
    copied_health = evidence_run / "p16_soak_health.json"
    copied_stats = evidence_run / "p16_soak_stats.log"
    copied_soak.write_text(
        json.dumps(
            {
                "checked_at": "2026-03-22T00:00:00Z",
                "passed": True,
                "health_summary": {"all_healthy": True, "totals": {}, "failures": {}},
                "sample_totals": {"docker_error_rate": 0.0},
                "checks": {"health_all_healthy": {"passed": True}},
            }
        ),
        encoding="utf-8",
    )
    copied_health.write_text('{"all_healthy":true}', encoding="utf-8")
    copied_stats.write_text("sample\n", encoding="utf-8")
    metadata = {
        "run_id": "run-3",
        "archived_at": "2026-03-23T00:00:10Z",
        "files": [
            {"name": "soak_report", "destination": str(copied_soak), "sha256": _sha256(copied_soak)},
            {"name": "health_report", "destination": str(copied_health), "sha256": _sha256(copied_health)},
            {"name": "stats_log", "destination": str(copied_stats), "sha256": _sha256(copied_stats)},
        ],
    }
    (evidence_run / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    output = tmp_path / "acceptance.json"
    result = _run_acceptance(soak_report=soak_report, evidence_path=evidence_run, output=output)
    assert result.returncode == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["accepted"] is False
    assert payload["acceptance_gate"]["checksum_verified"] is True
    assert payload["acceptance_gate"]["soak_report_checksum_matches_evidence"] is False
