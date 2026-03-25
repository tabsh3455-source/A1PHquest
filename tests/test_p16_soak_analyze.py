from pathlib import Path
import json
import subprocess
import sys


def _run_analyzer(
    *,
    health_file: Path,
    stats_file: Path,
    report_file: Path,
    max_total_mem_mib: float = 3584.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "deploy/p16_soak_analyze.py",
            "--health-report",
            str(health_file),
            "--stats-log",
            str(stats_file),
            "--report",
            str(report_file),
            "--duration-seconds",
            "120",
            "--stats-interval-seconds",
            "10",
            "--max-total-mem-mib",
            str(max_total_mem_mib),
        ],
        capture_output=True,
        text=True,
    )


def test_p16_soak_analyze_passes_with_healthy_inputs(tmp_path):
    health_file = tmp_path / "health.json"
    stats_file = tmp_path / "stats.log"
    report_file = tmp_path / "report.json"

    health_file.write_text(
        json.dumps(
            {
                "all_healthy": True,
                "failures": {
                    "http://127.0.0.1:8000/healthz": 0,
                    "http://127.0.0.1:8010/healthz": 0,
                },
                "totals": {},
            }
        ),
        encoding="utf-8",
    )
    stats_file.write_text(
        "\n".join(
            [
                "2026-03-23T00:00:00Z|a1phquest-api|12.0%|128MiB / 4GiB|10",
                "2026-03-23T00:00:00Z|a1phquest-worker-supervisor|8.0%|96MiB / 4GiB|8",
                "2026-03-23T00:00:00Z|a1phquest-postgres|6.0%|256MiB / 4GiB|12",
                "2026-03-23T00:00:10Z|a1phquest-api|10.0%|130MiB / 4GiB|10",
                "2026-03-23T00:00:10Z|a1phquest-worker-supervisor|7.5%|98MiB / 4GiB|8",
                "2026-03-23T00:00:10Z|a1phquest-postgres|5.0%|260MiB / 4GiB|12",
            ]
        ),
        encoding="utf-8",
    )

    result = _run_analyzer(health_file=health_file, stats_file=stats_file, report_file=report_file)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["passed"] is True
    assert payload["checks"]["health_all_healthy"]["passed"] is True
    assert payload["checks"]["peak_total_mem_mib"]["passed"] is True


def test_p16_soak_analyze_fails_on_threshold_breach(tmp_path):
    health_file = tmp_path / "health.json"
    stats_file = tmp_path / "stats.log"
    report_file = tmp_path / "report.json"

    health_file.write_text(
        json.dumps(
            {
                "all_healthy": True,
                "failures": {
                    "http://127.0.0.1:8000/healthz": 0,
                    "http://127.0.0.1:8010/healthz": 0,
                },
                "totals": {},
            }
        ),
        encoding="utf-8",
    )
    stats_file.write_text(
        "\n".join(
            [
                "2026-03-23T00:00:00Z|a1phquest-api|12.0%|2048MiB / 4GiB|10",
                "2026-03-23T00:00:00Z|a1phquest-worker-supervisor|8.0%|96MiB / 4GiB|8",
                # Missing postgres row also breaches required service check.
            ]
        ),
        encoding="utf-8",
    )

    result = _run_analyzer(
        health_file=health_file,
        stats_file=stats_file,
        report_file=report_file,
        max_total_mem_mib=512.0,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout.strip())
    assert payload["passed"] is False
    assert payload["checks"]["peak_total_mem_mib"]["passed"] is False
    assert payload["checks"]["required_service:a1phquest-postgres"]["passed"] is False
