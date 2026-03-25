from fastapi import HTTPException

from app.routers.orders import _lighter_payload_validation_error, _validate_lighter_signed_payload


def test_lighter_signed_payload_validation_passes_for_non_lighter():
    _validate_lighter_signed_payload(exchange="binance", exchange_payload=None, operation="submit")


def test_lighter_signed_payload_validation_rejects_missing_tx_fields():
    try:
        _validate_lighter_signed_payload(
            exchange="lighter",
            exchange_payload={"tx_type": 1},
            operation="submit",
        )
        raise AssertionError("Expected HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "tx_type" in str(exc.detail)


def test_lighter_signed_payload_validation_accepts_complete_payload():
    _validate_lighter_signed_payload(
        exchange="lighter",
        exchange_payload={"tx_type": 2, "tx_info": "{\"OrderIndex\": 1}"},
        operation="cancel",
    )


def test_lighter_signed_payload_validation_error_code_is_machine_readable():
    error = _lighter_payload_validation_error(
        exchange="lighter",
        exchange_payload={"tx_info": "{}"},
        operation="submit",
    )
    assert error is not None
    assert error[0] == "missing_tx_type"
