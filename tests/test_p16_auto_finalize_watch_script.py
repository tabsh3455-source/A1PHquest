from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import shlex
import shutil
import subprocess
import time

import pytest


def _to_wsl_path(path: Path) -> str:
    raw = str(path.resolve())
    if len(raw) >= 2 and raw[1] == ":":
        drive = raw[0].lower()
        rest = raw[2:].replace("\\", "/")
        return f"/mnt/{drive}{rest}"
    return raw.replace("\\", "/")


def _run_watcher(env_pairs: dict[str, str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    parts = [f"{key}={shlex.quote(value)}" for key, value in env_pairs.items()]
    cmd = " ".join(parts + ["bash", "deploy/p16_auto_finalize_watch.sh"])
    cwd = shlex.quote(_to_wsl_path(Path.cwd()))
    return subprocess.run(
        ["bash", "-lc", f"cd {cwd} && {cmd}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required for watcher script tests")
def test_auto_finalize_watch_writes_heartbeat_and_times_out(tmp_path: Path):
    pid_file = tmp_path / "watch.pid"
    log_file = tmp_path / "watch.log"
    status_file = tmp_path / "watch.status.json"
    progress_file = tmp_path / "soak_progress.json"
    progress_file.write_text(json.dumps({"status": "running"}), encoding="utf-8")

    env = {
        "PID_FILE": _to_wsl_path(pid_file),
        "LOG_FILE": _to_wsl_path(log_file),
        "STATUS_FILE": _to_wsl_path(status_file),
        "PROGRESS_FILE": _to_wsl_path(progress_file),
        "CHECK_INTERVAL_SECONDS": "1",
        "MAX_WAIT_SECONDS": "2",
        "SOAK_STATUS_CMD": "true",
        "SOAK_FINALIZE_CMD": "echo finalize_should_not_run",
    }
    result = _run_watcher(env, timeout=30)
    assert result.returncode == 1
    payload = json.loads(status_file.read_text(encoding="utf-8"))
    assert payload["last_action"] == "timeout"
    assert payload["elapsed_seconds"] >= 2
    assert "started_at" in payload
    assert "updated_at" in payload


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required for watcher script tests")
def test_auto_finalize_watch_restarts_stale_pid_and_finalizes(tmp_path: Path):
    pid_file = tmp_path / "watch.pid"
    log_file = tmp_path / "watch.log"
    status_file = tmp_path / "watch.status.json"
    finalized_marker = tmp_path / "finalized.marker"
    progress_file = tmp_path / "soak_progress.json"
    progress_file.write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    stale_proc = subprocess.Popen(["bash", "-lc", "sleep 120"])
    try:
        pid_file.write_text(str(stale_proc.pid), encoding="utf-8")
        stale_updated = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        status_file.write_text(
            json.dumps(
                {
                    "pid": stale_proc.pid,
                    "started_at": stale_updated,
                    "updated_at": stale_updated,
                    "elapsed_seconds": 600,
                    "last_action": "soak_running",
                }
            ),
            encoding="utf-8",
        )

        env = {
            "PID_FILE": _to_wsl_path(pid_file),
            "LOG_FILE": _to_wsl_path(log_file),
            "STATUS_FILE": _to_wsl_path(status_file),
            "PROGRESS_FILE": _to_wsl_path(progress_file),
            "HEARTBEAT_STALE_SECONDS": "1",
            "CHECK_INTERVAL_SECONDS": "1",
            "MAX_WAIT_SECONDS": "30",
            "SOAK_STATUS_CMD": "false",
            "SOAK_FINALIZE_CMD": f"touch {shlex.quote(_to_wsl_path(finalized_marker))}",
        }
        result = _run_watcher(env, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert finalized_marker.exists()

        payload = json.loads(status_file.read_text(encoding="utf-8"))
        assert payload["last_action"] == "finalize_completed"
        assert int(payload["pid"]) != stale_proc.pid
    finally:
        if stale_proc.poll() is None:
            stale_proc.terminate()
            try:
                stale_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                stale_proc.kill()
