from __future__ import annotations

from pathlib import Path
import importlib.util
import sys
from urllib import error as urllib_error


_HEALTH_MONITOR_PATH = Path(__file__).resolve().parents[1] / "deploy" / "health_monitor.py"
_SPEC = importlib.util.spec_from_file_location("health_monitor", _HEALTH_MONITOR_PATH)
assert _SPEC and _SPEC.loader
health_monitor = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = health_monitor
_SPEC.loader.exec_module(health_monitor)


class _FakeResponse:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_probe_returns_success_without_retry(monkeypatch):
    call_count = {"value": 0}

    def _urlopen(*args, **kwargs):
        call_count["value"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(health_monitor.request, "urlopen", _urlopen)

    result = health_monitor.probe("http://127.0.0.1:8000/healthz", retries=3)

    assert result.ok is True
    assert result.status_code == 200
    assert call_count["value"] == 1


def test_probe_retries_and_recovers(monkeypatch):
    call_count = {"value": 0}
    sleep_calls: list[float] = []

    def _urlopen(*args, **kwargs):
        call_count["value"] += 1
        if call_count["value"] == 1:
            raise urllib_error.URLError("transient timeout")
        return _FakeResponse(200)

    monkeypatch.setattr(health_monitor.request, "urlopen", _urlopen)
    monkeypatch.setattr(health_monitor.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    result = health_monitor.probe(
        "http://127.0.0.1:8000/healthz",
        retries=3,
        retry_delay_seconds=0.2,
    )

    assert result.ok is True
    assert result.status_code == 200
    assert call_count["value"] == 2
    assert sleep_calls == [0.2]


def test_probe_returns_last_failure_after_retry(monkeypatch):
    call_count = {"value": 0}

    def _urlopen(*args, **kwargs):
        call_count["value"] += 1
        raise urllib_error.URLError("network down")

    monkeypatch.setattr(health_monitor.request, "urlopen", _urlopen)
    monkeypatch.setattr(health_monitor.time, "sleep", lambda *_args, **_kwargs: None)

    result = health_monitor.probe("http://127.0.0.1:8000/healthz", retries=2)

    assert result.ok is False
    assert result.status_code is None
    assert "network down" in (result.error_message or "")
    assert call_count["value"] == 2
