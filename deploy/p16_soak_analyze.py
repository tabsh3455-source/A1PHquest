#!/usr/bin/env python3
"""Analyze P16 soak artifacts and produce a threshold-based verdict report."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterable


@dataclass(slots=True)
class ContainerSample:
    timestamp: str
    name: str
    cpu_pct: float
    mem_mib: float
    pids: int


def _parse_cpu_pct(raw: str) -> float:
    value = raw.strip().rstrip("%")
    return float(value or "0")


def _parse_mem_mib(raw: str) -> float:
    # docker stats memory value looks like: "97.14MiB / 15.58GiB"
    used = raw.split("/", maxsplit=1)[0].strip()
    if not used:
        return 0.0
    if used.endswith("KiB"):
        return float(used[:-3]) / 1024.0
    if used.endswith("MiB"):
        return float(used[:-3])
    if used.endswith("GiB"):
        return float(used[:-3]) * 1024.0
    if used.endswith("B"):
        return float(used[:-1]) / (1024.0 * 1024.0)
    raise ValueError(f"Unsupported memory unit: {raw}")


def _load_health(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8") or "{}")


def _load_stats(path: Path) -> tuple[list[ContainerSample], int]:
    samples: list[ContainerSample] = []
    docker_error_count = 0
    if not path.exists():
        return samples, docker_error_count

    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        if "|error|docker_stats_failed" in text:
            docker_error_count += 1
            continue

        # Expected format:
        # timestamp|name|cpu|mem|pids
        parts = text.split("|")
        if len(parts) != 5:
            continue
        timestamp, name, cpu_raw, mem_raw, pids_raw = parts
        try:
            sample = ContainerSample(
                timestamp=timestamp,
                name=name,
                cpu_pct=_parse_cpu_pct(cpu_raw),
                mem_mib=_parse_mem_mib(mem_raw),
                pids=int(pids_raw.strip()),
            )
        except Exception:
            # Skip malformed lines while preserving deterministic behavior.
            continue
        samples.append(sample)
    return samples, docker_error_count


def _latest_per_timestamp(samples: Iterable[ContainerSample]) -> dict[str, dict[str, ContainerSample]]:
    grouped: dict[str, dict[str, ContainerSample]] = defaultdict(dict)
    for sample in samples:
        grouped[sample.timestamp][sample.name] = sample
    return grouped


def _bool_check(*, passed: bool, detail: str, actual: float | int | str | bool, threshold: float | int | str | None) -> dict:
    return {
        "passed": bool(passed),
        "detail": detail,
        "actual": actual,
        "threshold": threshold,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze soak health/docker stats and emit verdict JSON report.")
    parser.add_argument("--health-report", required=True)
    parser.add_argument("--stats-log", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--duration-seconds", type=int, default=0)
    parser.add_argument("--stats-interval-seconds", type=int, default=0)
    parser.add_argument("--required-services", default="a1phquest-api,a1phquest-worker-supervisor,a1phquest-postgres")
    parser.add_argument("--max-total-mem-mib", type=float, default=3584.0)
    parser.add_argument("--max-container-mem-mib", type=float, default=1024.0)
    parser.add_argument("--max-cpu-pct", type=float, default=190.0)
    parser.add_argument("--max-pids", type=int, default=256)
    parser.add_argument("--max-docker-error-rate", type=float, default=0.05)
    args = parser.parse_args()

    health = _load_health(Path(args.health_report))
    samples, docker_error_count = _load_stats(Path(args.stats_log))
    required_services = [name.strip() for name in args.required_services.split(",") if name.strip()]

    samples_by_container: dict[str, list[ContainerSample]] = defaultdict(list)
    for sample in samples:
        samples_by_container[sample.name].append(sample)

    container_peaks: dict[str, dict[str, float | int]] = {}
    for name, rows in samples_by_container.items():
        container_peaks[name] = {
            "cpu_pct_max": max(row.cpu_pct for row in rows),
            "mem_mib_max": round(max(row.mem_mib for row in rows), 2),
            "pids_max": max(row.pids for row in rows),
            "sample_count": len(rows),
        }

    snapshots = _latest_per_timestamp(samples)
    peak_total_mem_mib = 0.0
    for by_name in snapshots.values():
        total_mib = sum(row.mem_mib for row in by_name.values())
        peak_total_mem_mib = max(peak_total_mem_mib, total_mib)
    peak_total_mem_mib = round(peak_total_mem_mib, 2)

    total_rows = len(samples) + docker_error_count
    docker_error_rate = (docker_error_count / total_rows) if total_rows else 0.0

    checks: dict[str, dict] = {}
    checks["health_all_healthy"] = _bool_check(
        passed=bool(health.get("all_healthy", False)),
        detail="health_monitor all_healthy must be true",
        actual=bool(health.get("all_healthy", False)),
        threshold=True,
    )
    checks["docker_error_rate"] = _bool_check(
        passed=docker_error_rate <= args.max_docker_error_rate,
        detail="docker stats collection error rate",
        actual=round(docker_error_rate, 4),
        threshold=args.max_docker_error_rate,
    )
    checks["peak_total_mem_mib"] = _bool_check(
        passed=peak_total_mem_mib <= args.max_total_mem_mib,
        detail="peak total container memory (MiB)",
        actual=peak_total_mem_mib,
        threshold=args.max_total_mem_mib,
    )

    for service in required_services:
        key = f"required_service:{service}"
        present = service in samples_by_container and len(samples_by_container[service]) > 0
        checks[key] = _bool_check(
            passed=present,
            detail=f"required service {service} observed in stats log",
            actual=present,
            threshold=True,
        )

    for name, peak in container_peaks.items():
        checks[f"{name}:cpu_pct_max"] = _bool_check(
            passed=float(peak["cpu_pct_max"]) <= args.max_cpu_pct,
            detail=f"{name} max cpu pct",
            actual=float(peak["cpu_pct_max"]),
            threshold=args.max_cpu_pct,
        )
        checks[f"{name}:mem_mib_max"] = _bool_check(
            passed=float(peak["mem_mib_max"]) <= args.max_container_mem_mib,
            detail=f"{name} max memory mib",
            actual=float(peak["mem_mib_max"]),
            threshold=args.max_container_mem_mib,
        )
        checks[f"{name}:pids_max"] = _bool_check(
            passed=int(peak["pids_max"]) <= args.max_pids,
            detail=f"{name} max pids",
            actual=int(peak["pids_max"]),
            threshold=args.max_pids,
        )

    passed = all(item["passed"] for item in checks.values())
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "duration_seconds": args.duration_seconds,
        "stats_interval_seconds": args.stats_interval_seconds,
        "required_services": required_services,
        "sample_totals": {
            "stats_rows": len(samples),
            "docker_error_rows": docker_error_count,
            "docker_error_rate": round(docker_error_rate, 4),
            "timestamp_buckets": len(snapshots),
        },
        "health_summary": {
            "all_healthy": bool(health.get("all_healthy", False)),
            "failures": health.get("failures", {}),
            "totals": health.get("totals", {}),
        },
        "peaks": {
            "peak_total_mem_mib": peak_total_mem_mib,
            "containers": container_peaks,
        },
        "checks": checks,
    }

    report_path = Path(args.report)
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
