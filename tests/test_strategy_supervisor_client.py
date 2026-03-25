from app.services.strategy_supervisor import StrategySupervisorClient


def test_supervisor_client_timeout_reads_env(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_HTTP_TIMEOUT_SECONDS", "37.5")
    client = StrategySupervisorClient(base_url="http://example.com")
    assert client.timeout == 37.5


def test_supervisor_client_timeout_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_HTTP_TIMEOUT_SECONDS", "not-a-number")
    client = StrategySupervisorClient(base_url="http://example.com")
    assert client.timeout == 20.0


def test_runtime_state_parses_observability_fields():
    state = StrategySupervisorClient._to_runtime_state(
        {
            "runtime_ref": "rt-1",
            "status": "running",
            "process_id": "proc-1",
            "last_event_seq": 8,
            "order_submitted_count": 3,
            "order_update_count": 5,
            "trade_fill_count": 2,
            "recent_events": [{"seq": 8, "type": "trade_filled", "payload": {}}],
        }
    )
    assert state.runtime_ref == "rt-1"
    assert state.last_event_seq == 8
    assert state.order_submitted_count == 3
    assert state.order_update_count == 5
    assert state.trade_fill_count == 2
    assert state.recent_events and state.recent_events[0]["type"] == "trade_filled"


def test_supervisor_client_includes_shared_token_header():
    client = StrategySupervisorClient(base_url="http://example.com", shared_token="abc123")
    headers = client._build_auth_headers()
    assert headers["X-Supervisor-Token"] == "abc123"
