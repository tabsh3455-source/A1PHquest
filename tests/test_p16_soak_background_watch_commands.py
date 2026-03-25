from __future__ import annotations

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


def _run_bg(cmd: str, env_pairs: dict[str, str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    parts = [f"{key}={shlex.quote(value)}" for key, value in env_pairs.items()]
    cwd = shlex.quote(_to_wsl_path(Path.cwd()))
    full_cmd = " ".join(parts + ["bash", "deploy/p16_soak_background.sh", cmd])
    return subprocess.run(
        ["bash", "-lc", f"cd {cwd} && {full_cmd}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required for watcher command tests")
def test_watch_status_reads_status_file_when_not_running(tmp_path: Path):
    watch_pid = tmp_path / "watch.pid"
    watch_log = tmp_path / "watch.log"
    watch_status = tmp_path / "watch.status.json"
    watch_status.write_text(
        json.dumps(
            {
                "started_at": "2026-03-24T00:00:00Z",
                "updated_at": "2026-03-24T00:01:00Z",
                "elapsed_seconds": 60,
                "last_action": "soak_running",
                "soak_state": "running",
            }
        ),
        encoding="utf-8",
    )
    env = {
        "WATCH_PID_FILE": _to_wsl_path(watch_pid),
        "WATCH_LOG_FILE": _to_wsl_path(watch_log),
        "WATCH_STATUS_FILE": _to_wsl_path(watch_status),
    }
    result = _run_bg("watch-status", env)
    assert result.returncode == 1
    assert "watcher_last_action=soak_running" in result.stdout
    assert "auto-finalize watcher not running" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required for watcher command tests")
def test_watch_start_tracks_and_auto_finalizes(tmp_path: Path):
    watch_pid = tmp_path / "watch.pid"
    watch_log = tmp_path / "watch.log"
    watch_status = tmp_path / "watch.status.json"
    running_flag = tmp_path / "running.flag"
    finalized_marker = tmp_path / "finalized.marker"
    progress_file = tmp_path / "soak_progress.json"

    running_flag.write_text("1", encoding="utf-8")
    progress_file.write_text(json.dumps({"status": "running"}), encoding="utf-8")
    running_flag_wsl = _to_wsl_path(running_flag)
    finalized_wsl = _to_wsl_path(finalized_marker)

    env = {
        "WATCH_PID_FILE": _to_wsl_path(watch_pid),
        "WATCH_LOG_FILE": _to_wsl_path(watch_log),
        "WATCH_STATUS_FILE": _to_wsl_path(watch_status),
        "PROGRESS_FILE": _to_wsl_path(progress_file),
        "WATCH_CHECK_INTERVAL_SECONDS": "1",
        "WATCH_MAX_WAIT_SECONDS": "30",
        "WATCH_HEARTBEAT_STALE_SECONDS": "180",
        "WATCH_SOAK_STATUS_CMD": f"test -f {shlex.quote(running_flag_wsl)}",
        "WATCH_SOAK_FINALIZE_CMD": f"touch {shlex.quote(finalized_wsl)}",
    }

    start_result = _run_bg("watch-start", env)
    assert start_result.returncode == 0, start_result.stdout + start_result.stderr

    status_running = _run_bg("watch-status", env)
    assert status_running.returncode == 0
    assert "auto-finalize watcher running" in status_running.stdout

    running_flag.unlink()
    progress_file.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    for _ in range(20):
        if finalized_marker.exists():
            break
        time.sleep(0.3)
    assert finalized_marker.exists()

    # Watcher should exit after finalize completed.
    for _ in range(20):
        status_done = _run_bg("watch-status", env)
        if status_done.returncode != 0:
            break
        time.sleep(0.3)
    status_done = _run_bg("watch-status", env)
    assert status_done.returncode == 1
    assert "watcher_last_action=finalize_completed" in status_done.stdout

    stop_result = _run_bg("watch-stop", env)
    assert stop_result.returncode == 0
