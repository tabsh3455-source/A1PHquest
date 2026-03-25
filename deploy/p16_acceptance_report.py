#!/usr/bin/env python3
"""Build final P16 acceptance report from soak outputs and archived evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
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


def _discover_latest_evidence_dir(root: Path) -> Path | None:
    if not root.exists():
        return None
    candidates = [item for item in root.iterdir() if item.is_dir() and (item / "metadata.json").exists()]
    if not candidates:
        return None
    # Latest modified evidence folder is treated as current run.
    return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)[0]


def _verify_evidence_checksums(run_dir: Path, metadata: dict[str, Any]) -> tuple[bool, list[dict[str, str]]]:
    mismatches: list[dict[str, str]] = []
    for entry in metadata.get("files", []):
        if not isinstance(entry, dict):
            continue
        destination_text = str(entry.get("destination") or "").strip()
        # Historical metadata may contain Windows-style separators even when
        # verification runs on Linux VPS, so normalize path first.
        normalized_text = destination_text.replace("\\", "/")
        destination = Path(normalized_text)
        expected = str(entry.get("sha256") or "").strip().lower()
        candidates: list[Path] = []
        if destination.is_absolute():
            candidates.append(destination)
        else:
            candidates.append(Path(normalized_text))
            candidates.append(run_dir / Path(normalized_text))
            candidates.append(run_dir / Path(normalized_text).name)
        existing_path: Path | None = None
        for candidate in candidates:
            if candidate.exists():
                existing_path = candidate
                break
        if not existing_path:
            mismatches.append(
                {
                    "file": destination_text or str(destination),
                    "reason": "missing",
                }
            )
            continue
        actual = _sha256(existing_path).lower()
        if expected and actual != expected:
            mismatches.append(
                {
                    "file": str(existing_path),
                    "reason": "checksum_mismatch",
                    "expected": expected,
                    "actual": actual,
                }
            )
    return len(mismatches) == 0, mismatches


def _extract_evidence_soak_checksum(metadata: dict[str, Any]) -> str | None:
    for entry in metadata.get("files", []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip().lower()
        destination = str(entry.get("destination") or "").strip().lower().replace("\\", "/")
        if name == "soak_report" or destination.endswith("p16_soak_report.json"):
            checksum = str(entry.get("sha256") or "").strip().lower()
            if checksum:
                return checksum
    return None


def _build_url_availability(health_summary: dict[str, Any]) -> dict[str, float]:
    totals = health_summary.get("totals") if isinstance(health_summary.get("totals"), dict) else {}
    failures = health_summary.get("failures") if isinstance(health_summary.get("failures"), dict) else {}
    result: dict[str, float] = {}
    for url, total in totals.items():
        try:
            total_count = int(total)
        except (TypeError, ValueError):
            continue
        if total_count <= 0:
            continue
        fail_count = 0
        try:
            fail_count = int(failures.get(url, 0))
        except (TypeError, ValueError):
            fail_count = 0
        success_ratio = max(0.0, min(1.0, (total_count - fail_count) / total_count))
        result[str(url)] = round(success_ratio, 4)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate final P16 acceptance report with evidence verification.")
    parser.add_argument("--soak-report", default="deploy/p16_soak_report.json")
    parser.add_argument("--evidence-dir", default="deploy/evidence/p16")
    parser.add_argument("--evidence-path", default="")
    parser.add_argument("--output", default="deploy/p16_acceptance_report.json")
    args = parser.parse_args()

    soak_report_path = Path(args.soak_report)
    output_path = Path(args.output)
    if not soak_report_path.exists():
        print(
            json.dumps(
                {"accepted": False, "reason": "missing_soak_report", "path": str(soak_report_path)},
                ensure_ascii=False,
            )
        )
        return 1

    soak_report = _load_json(soak_report_path)
    evidence_path_arg = str(args.evidence_path or "").strip()
    if evidence_path_arg:
        evidence_dir = Path(evidence_path_arg)
    else:
        discovered = _discover_latest_evidence_dir(Path(args.evidence_dir))
        if not discovered:
            print(
                json.dumps(
                    {"accepted": False, "reason": "missing_evidence_dir", "path": args.evidence_dir},
                    ensure_ascii=False,
                )
            )
            return 1
        evidence_dir = discovered

    metadata_path = evidence_dir / "metadata.json"
    if not metadata_path.exists():
        print(
            json.dumps(
                {"accepted": False, "reason": "missing_metadata", "path": str(metadata_path)},
                ensure_ascii=False,
            )
        )
        return 1

    metadata = _load_json(metadata_path)
    checksum_verified, checksum_mismatches = _verify_evidence_checksums(evidence_dir, metadata)
    soak_report_checksum = _sha256(soak_report_path).lower()
    evidence_soak_checksum = _extract_evidence_soak_checksum(metadata)
    soak_report_checksum_matches_evidence = bool(
        evidence_soak_checksum and evidence_soak_checksum == soak_report_checksum
    )
    if evidence_soak_checksum and not soak_report_checksum_matches_evidence:
        checksum_mismatches.append(
            {
                "file": str(soak_report_path),
                "reason": "soak_report_checksum_not_matching_evidence",
                "expected": evidence_soak_checksum,
                "actual": soak_report_checksum,
            }
        )
    checks = soak_report.get("checks") if isinstance(soak_report.get("checks"), dict) else {}
    failed_checks = [
        name
        for name, payload in checks.items()
        if isinstance(payload, dict) and not bool(payload.get("passed", False))
    ]
    required_service_failures = [name for name in failed_checks if name.startswith("required_service:")]
    health_summary = soak_report.get("health_summary") if isinstance(soak_report.get("health_summary"), dict) else {}
    sample_totals = soak_report.get("sample_totals") if isinstance(soak_report.get("sample_totals"), dict) else {}

    accepted = (
        bool(soak_report.get("passed", False))
        and checksum_verified
        and not failed_checks
        and soak_report_checksum_matches_evidence
    )
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "accepted": accepted,
        "acceptance_gate": {
            "soak_passed": bool(soak_report.get("passed", False)),
            "checksum_verified": checksum_verified,
            "soak_report_checksum_matches_evidence": soak_report_checksum_matches_evidence,
            "failed_checks_count": len(failed_checks),
            "required_service_failures": required_service_failures,
        },
        "availability": {
            "all_healthy": bool(health_summary.get("all_healthy", False)),
            "url_success_ratio": _build_url_availability(health_summary),
            "health_failures": health_summary.get("failures", {}),
        },
        "resources": {
            "peaks": (soak_report.get("peaks") if isinstance(soak_report.get("peaks"), dict) else {}),
            "sample_totals": sample_totals,
        },
        "errors": {
            "failed_checks": failed_checks,
            "checksum_mismatches": checksum_mismatches,
            "docker_error_rate": sample_totals.get("docker_error_rate"),
        },
        "evidence": {
            "run_id": metadata.get("run_id"),
            "archived_at": metadata.get("archived_at"),
            "path": str(evidence_dir),
            "metadata_path": str(metadata_path),
            "files": metadata.get("files", []),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
