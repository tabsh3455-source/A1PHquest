#!/usr/bin/env python3
"""Finalize P16 long-run soak by producing acceptance and optional RC reports."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _discover_latest_evidence_dir(root: Path) -> Path | None:
    if not root.exists():
        return None
    candidates = [item for item in root.iterdir() if item.is_dir() and (item / "metadata.json").exists()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)[0]


def _emit(output: Path, payload: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def _normalize_path(path_text: str) -> str:
    normalized = str(Path(path_text)).replace("\\", "/").rstrip("/")
    if len(normalized) >= 2 and normalized[1] == ":":
        normalized = normalized[0].lower() + normalized[1:]
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize soak run with acceptance + optional RC gate.")
    parser.add_argument("--progress-file", default="deploy/p16_soak_progress.json")
    parser.add_argument("--evidence-dir", default="deploy/evidence/p16")
    parser.add_argument("--acceptance-report", default="deploy/p16_acceptance_report.json")
    parser.add_argument("--p20-rc-report", default="deploy/p20_rc_report.json")
    parser.add_argument("--p20-checklist-report", default="deploy/p20_release_checklist.json")
    parser.add_argument("--p20-release-manifest", default="deploy/p20_release_manifest.json")
    parser.add_argument("--output", default="deploy/p16_finalize_report.json")
    parser.add_argument(
        "--run-p20-rc",
        type=int,
        choices=[0, 1],
        default=1,
        help="1: run deploy/p20_rc_gate.sh with RUN_RELEASE_GATE=0 after acceptance",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    progress_path = Path(args.progress_file)
    if not progress_path.exists():
        _emit(
            output_path,
            {
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "ok": False,
                "reason": "missing_progress_file",
                "progress_file": str(progress_path),
            },
        )
        return 1

    progress = _load_json(progress_path)
    progress_status = str(progress.get("status") or "").strip().lower()
    if progress_status == "running":
        _emit(
            output_path,
            {
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "ok": False,
                "reason": "soak_still_running",
                "progress": progress,
            },
        )
        return 1
    if progress_status not in {"completed", "failed", "interrupted"}:
        _emit(
            output_path,
            {
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "ok": False,
                "reason": "invalid_progress_status",
                "progress_status": progress_status,
            },
        )
        return 1
    if progress_status != "completed":
        _emit(
            output_path,
            {
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "ok": False,
                "reason": "soak_not_completed",
                "progress_status": progress_status,
            },
        )
        return 1

    evidence_root = Path(args.evidence_dir)
    evidence_path = _discover_latest_evidence_dir(evidence_root)
    if not evidence_path:
        _emit(
            output_path,
            {
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "ok": False,
                "reason": "missing_evidence_bundle",
                "evidence_dir": str(evidence_root),
            },
        )
        return 1
    soak_report_path = evidence_path / "p16_soak_report.json"
    if not soak_report_path.exists():
        _emit(
            output_path,
            {
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "ok": False,
                "reason": "missing_soak_report_in_evidence",
                "evidence_path": str(evidence_path),
            },
        )
        return 1

    acceptance_cmd = [
        sys.executable,
        "deploy/p16_acceptance_report.py",
        "--soak-report",
        str(soak_report_path),
        "--evidence-path",
        str(evidence_path),
        "--output",
        str(Path(args.acceptance_report)),
    ]
    acceptance_proc = subprocess.run(acceptance_cmd, capture_output=True, text=True, check=False)
    if acceptance_proc.returncode != 0:
        _emit(
            output_path,
            {
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "ok": False,
                "reason": "acceptance_report_failed",
                "evidence_path": str(evidence_path),
                "acceptance_stdout": acceptance_proc.stdout.strip(),
                "acceptance_stderr": acceptance_proc.stderr.strip(),
            },
        )
        return 1
    acceptance_payload = _load_json(Path(args.acceptance_report))
    acceptance_evidence = (
        acceptance_payload.get("evidence") if isinstance(acceptance_payload.get("evidence"), dict) else {}
    )
    acceptance_evidence_path = str(acceptance_evidence.get("path") or "").strip()
    acceptance_run_id = str(acceptance_evidence.get("run_id") or "").strip()
    if not acceptance_evidence_path or not acceptance_run_id:
        _emit(
            output_path,
            {
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "ok": False,
                "reason": "acceptance_missing_evidence_binding",
                "evidence_path": str(evidence_path),
                "acceptance_report_path": str(Path(args.acceptance_report)),
            },
        )
        return 1
    if _normalize_path(acceptance_evidence_path) != _normalize_path(str(evidence_path)):
        _emit(
            output_path,
            {
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "ok": False,
                "reason": "acceptance_evidence_path_mismatch",
                "evidence_path": str(evidence_path),
                "acceptance_evidence_path": acceptance_evidence_path,
            },
        )
        return 1

    p20_proc: subprocess.CompletedProcess[str] | None = None
    checklist_proc: subprocess.CompletedProcess[str] | None = None
    manifest_proc: subprocess.CompletedProcess[str] | None = None
    p20_payload: dict[str, Any] | None = None
    checklist_payload: dict[str, Any] | None = None
    manifest_payload: dict[str, Any] | None = None
    if args.run_p20_rc == 1:
        env = os.environ.copy()
        env["RUN_RELEASE_GATE"] = "0"
        env["RUN_P20_CHECKLIST"] = "0"
        env["EVIDENCE_PATH"] = str(evidence_path)
        env["P16_ACCEPTANCE_REPORT"] = str(Path(args.acceptance_report))
        env["REPORT_FILE"] = str(Path(args.p20_rc_report))
        p20_proc = subprocess.run(
            ["bash", "deploy/p20_rc_gate.sh"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if p20_proc.returncode != 0:
            _emit(
                output_path,
                {
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "ok": False,
                    "reason": "p20_rc_gate_failed",
                    "evidence_path": str(evidence_path),
                    "p20_stdout": p20_proc.stdout.strip(),
                    "p20_stderr": p20_proc.stderr.strip(),
                },
            )
            return 1
        p20_payload = _load_json(Path(args.p20_rc_report))
        rc_evidence_path = str(p20_payload.get("evidence_path") or "").strip()
        rc_evidence_run_id = str(p20_payload.get("evidence_run_id") or "").strip()
        if _normalize_path(rc_evidence_path) != _normalize_path(str(evidence_path)) or rc_evidence_run_id != acceptance_run_id:
            _emit(
                output_path,
                {
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "ok": False,
                    "reason": "p20_rc_evidence_binding_mismatch",
                    "evidence_path": str(evidence_path),
                    "acceptance_run_id": acceptance_run_id,
                    "p20_rc_evidence_path": rc_evidence_path,
                    "p20_rc_evidence_run_id": rc_evidence_run_id,
                },
            )
            return 1

        checklist_cmd = [
            sys.executable,
            "deploy/p20_release_checklist.py",
            "--release-gate-report",
            "deploy/release_gate_report.json",
            "--p16-acceptance-report",
            str(Path(args.acceptance_report)),
            "--p20-rc-report",
            str(Path(args.p20_rc_report)),
            "--soak-progress",
            str(progress_path),
            "--output",
            str(Path(args.p20_checklist_report)),
            "--required-evidence-path",
            str(evidence_path),
            "--required-run-id",
            acceptance_run_id,
            "--strict",
        ]
        checklist_proc = subprocess.run(checklist_cmd, capture_output=True, text=True, check=False)
        if checklist_proc.returncode != 0:
            _emit(
                output_path,
                {
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "ok": False,
                    "reason": "p20_release_checklist_failed",
                    "evidence_path": str(evidence_path),
                    "p20_stdout": p20_proc.stdout.strip(),
                    "checklist_stdout": checklist_proc.stdout.strip(),
                    "checklist_stderr": checklist_proc.stderr.strip(),
                },
            )
            return 1
        checklist_payload = _load_json(Path(args.p20_checklist_report))

        manifest_cmd = [
            sys.executable,
            "deploy/p20_release_manifest.py",
            "--release-gate-report",
            "deploy/release_gate_report.json",
            "--p16-acceptance-report",
            str(Path(args.acceptance_report)),
            "--p20-rc-report",
            str(Path(args.p20_rc_report)),
            "--p20-checklist-report",
            str(Path(args.p20_checklist_report)),
            "--output",
            str(Path(args.p20_release_manifest)),
        ]
        manifest_proc = subprocess.run(manifest_cmd, capture_output=True, text=True, check=False)
        if manifest_proc.returncode != 0:
            _emit(
                output_path,
                {
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "ok": False,
                    "reason": "p20_release_manifest_failed",
                    "evidence_path": str(evidence_path),
                    "manifest_stdout": manifest_proc.stdout.strip(),
                    "manifest_stderr": manifest_proc.stderr.strip(),
                },
            )
            return 1
        manifest_payload = _load_json(Path(args.p20_release_manifest))

    result = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "ok": True,
        "progress_status": progress_status,
        "evidence_path": str(evidence_path),
        "evidence_run_id": acceptance_run_id,
        "acceptance_report_path": str(Path(args.acceptance_report)),
        "acceptance_passed": bool(acceptance_payload.get("accepted", False)),
        "p20_rc_executed": bool(args.run_p20_rc == 1),
        "p20_rc_report_path": str(Path(args.p20_rc_report)),
        "p20_checklist_report_path": str(Path(args.p20_checklist_report)),
        "p20_release_manifest_path": str(Path(args.p20_release_manifest)),
        "p20_checklist_ready_for_release": (
            bool(checklist_payload.get("ready_for_release", False))
            if checklist_payload is not None
            else None
        ),
    }
    if p20_proc is not None:
        result["p20_stdout"] = p20_proc.stdout.strip()
    if checklist_proc is not None:
        result["p20_checklist_stdout"] = checklist_proc.stdout.strip()
    if manifest_proc is not None:
        result["p20_release_manifest_stdout"] = manifest_proc.stdout.strip()
    if p20_payload is not None:
        result["p20_rc_checklist_status"] = p20_payload.get("checklist_status")
    if manifest_payload is not None:
        result["p20_manifest_ready_for_release"] = manifest_payload.get("ready_for_release")
    _emit(output_path, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
