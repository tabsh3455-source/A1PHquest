from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys


def _run_archive(
    *,
    soak_report: Path,
    health_report: Path,
    stats_log: Path,
    evidence_dir: Path,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "deploy/p16_archive_evidence.py",
        "--soak-report",
        str(soak_report),
        "--health-report",
        str(health_report),
        "--stats-log",
        str(stats_log),
        "--evidence-dir",
        str(evidence_dir),
    ]
    if extra_args:
        command.extend(extra_args)
    return subprocess.run(command, capture_output=True, text=True)


def test_archive_evidence_creates_timestamped_bundle(tmp_path):
    soak_report = tmp_path / "soak_report.json"
    health_report = tmp_path / "health_report.json"
    stats_log = tmp_path / "stats.log"
    evidence_dir = tmp_path / "evidence"

    soak_report.write_text(
        json.dumps({"checked_at": "2026-03-23T12:00:00Z", "passed": True, "checks": {}}),
        encoding="utf-8",
    )
    health_report.write_text(json.dumps({"all_healthy": True}), encoding="utf-8")
    stats_log.write_text("2026-03-23T12:00:00Z|a1phquest-api|1.0%|10MiB / 4GiB|5\n", encoding="utf-8")

    result = _run_archive(
        soak_report=soak_report,
        health_report=health_report,
        stats_log=stats_log,
        evidence_dir=evidence_dir,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["archived"] is True
    run_path = Path(payload["evidence_path"])
    assert run_path.exists()
    assert (run_path / "soak_report.json").exists()
    assert (run_path / "health_report.json").exists()
    assert (run_path / "stats.log").exists()
    metadata = json.loads((run_path / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["soak_passed"] is True
    assert len(metadata["files"]) == 3
    assert all(entry["sha256"] for entry in metadata["files"])


def test_archive_evidence_rejects_failed_report_without_override(tmp_path):
    soak_report = tmp_path / "soak_report.json"
    health_report = tmp_path / "health_report.json"
    stats_log = tmp_path / "stats.log"
    evidence_dir = tmp_path / "evidence"

    soak_report.write_text(json.dumps({"checked_at": "2026-03-23T12:00:00Z", "passed": False}), encoding="utf-8")
    health_report.write_text(json.dumps({"all_healthy": False}), encoding="utf-8")
    stats_log.write_text("2026-03-23T12:00:00Z|error|docker_stats_failed\n", encoding="utf-8")

    result = _run_archive(
        soak_report=soak_report,
        health_report=health_report,
        stats_log=stats_log,
        evidence_dir=evidence_dir,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout.strip())
    assert payload["archived"] is False
    assert payload["reason"] == "failed_soak_report"
    assert not evidence_dir.exists()


def test_archive_evidence_allows_failed_report_with_override(tmp_path):
    soak_report = tmp_path / "soak_report.json"
    health_report = tmp_path / "health_report.json"
    stats_log = tmp_path / "stats.log"
    evidence_dir = tmp_path / "evidence"

    soak_report.write_text(
        json.dumps(
            {
                "checked_at": "2026-03-23T12:00:00Z",
                "passed": False,
                "checks": {
                    "health_all_healthy": {"passed": False},
                },
            }
        ),
        encoding="utf-8",
    )
    health_report.write_text(json.dumps({"all_healthy": False}), encoding="utf-8")
    stats_log.write_text("2026-03-23T12:00:00Z|error|docker_stats_failed\n", encoding="utf-8")

    result = _run_archive(
        soak_report=soak_report,
        health_report=health_report,
        stats_log=stats_log,
        evidence_dir=evidence_dir,
        extra_args=["--allow-failed", "--label", "vps-nightly"],
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    run_path = Path(payload["evidence_path"])
    assert run_path.name.endswith("_vps-nightly")
    metadata = json.loads((run_path / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["soak_passed"] is False
    assert metadata["failed_checks"] == ["health_all_healthy"]
