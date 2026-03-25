from app.events import build_ws_event


def test_build_ws_event_contains_envelope_and_payload():
    event = build_ws_event(
        event_type="strategy_runtime_update",
        resource_id="rt-1",
        payload={"strategy_id": 11, "status": "running"},
    )
    assert event["type"] == "strategy_runtime_update"
    assert event["resource_id"] == "rt-1"
    assert "timestamp" in event
    assert event["payload"]["strategy_id"] == 11
    # Backward compatibility: payload keys are still flattened.
    assert event["strategy_id"] == 11
    assert event["status"] == "running"


def test_build_ws_event_keeps_optional_dedupe_key():
    event = build_ws_event(
        event_type="trade_filled",
        resource_id="ord-1",
        payload={"order_id": "ord-1"},
        dedupe_key="trade:ord-1:1",
    )
    assert event["dedupe_key"] == "trade:ord-1:1"
