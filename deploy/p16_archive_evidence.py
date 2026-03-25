#!/usr/bin/env python3
"""Archive P16 soak artifacts into a timestamped evidence directory."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            chunk = file.read(8192)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sanitize_label(label: str) -> str:
    # Keep labels filesystem-safe and deterministic for shell-friendly evidence paths.
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", label.strip())
    cleaned = cleaned.strip("-_")
    return cleaned or "run"


def _derive_run_id(soak_report: dict[str, Any], label: str | None) -> str:
    checked_at = soak_report.get("checked_at")
    if isinstance(checked_at, str) and checked_at:
        try:
            run_dt = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
        except ValueError:
            run_dt = datetime.now(timezone.utc)
    else:
        run_dt = datetime.now(timezone.utc)
    run_stamp = run_dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if label:
        return f"{run_stamp}_{_sanitize_label(label)}"
    return run_stamp


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive soak outputs with checksums.")
    parser.add_argument("--soak-report", default="deploy/p16_soak_report.json")
    parser.add_argument("--health-report", default="deploy/p16_soak_health.json")
    parser.add_argument("--stats-log", default="deploy/p16_soak_stats.log")
    parser.add_argument("--evidence-dir", default="deploy/evidence/p16")
    parser.add_argument("--label", default="", help="Optional short label appended to run id.")
    parser.add_argument(
        "--allow-failed",
        action="store_true",
        help="Allow archiving even when soak report marks run as failed.",
    )
    args = parser.parse_args()

    soak_report_path = Path(args.soak_report)
    health_report_path = Path(args.health_report)
    stats_log_path = Path(args.stats_log)
    evidence_dir = Path(args.evidence_dir)

    for file_path in (soak_report_path, health_report_path, stats_log_path):
        if not file_path.exists():
            print(
                json.dumps(
                    {
                        "archived": False,
                        "reason": "missing_file",
                        "missing": str(file_path),
                    },
                    ensure_ascii=False,
                )
            )
            return 1

    soak_report = _load_json(soak_report_path)
    passed = bool(soak_report.get("passed", False))
    if not passed and not args.allow_failed:
        print(
            json.dumps(
                {
                    "archived": False,
                    "reason": "failed_soak_report",
                    "allow_failed": False,
                },
                ensure_ascii=False,
            )
        )
        return 1

    run_id = _derive_run_id(soak_report, args.label)
    run_dir = evidence_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    source_files = {
        "soak_report": soak_report_path,
        "health_report": health_report_path,
        "stats_log": stats_log_path,
    }
    file_entries: list[dict[str, Any]] = []
    for key, source_path in source_files.items():
        destination = run_dir / source_path.name
        shutil.copy2(source_path, destination)
        file_entries.append(
            {
                "name": key,
                "source": str(source_path),
                "destination": str(destination),
                "size_bytes": destination.stat().st_size,
                "sha256": _sha256(destination),
            }
        )

    failed_checks = [
        check_name
        for check_name, check_data in (soak_report.get("checks") or {}).items()
        if isinstance(check_data, dict) and not bool(check_data.get("passed", False))
    ]
    metadata = {
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "label": args.label or None,
        "soak_checked_at": soak_report.get("checked_at"),
        "soak_passed": passed,
        "failed_checks": failed_checks,
        "files": file_entries,
    }
    metadata_path = run_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "archived": True,
                "run_id": run_id,
                "evidence_path": str(run_dir),
                "metadata_path": str(metadata_path),
                "soak_passed": passed,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
