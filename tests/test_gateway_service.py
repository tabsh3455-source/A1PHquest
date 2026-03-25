import httpx

from app.models import ExchangeAccount
from app.services.gateway_service import GatewayService, LighterSendTxResult


def _build_account(exchange: str, is_testnet: bool = False) -> ExchangeAccount:
    return ExchangeAccount(
        user_id=1,
        exchange=exchange,
        account_alias=f"{exchange}-acc",
        api_key_encrypted="x",
        api_secret_encrypted="y",
        is_testnet=is_testnet,
    )


def _response(status_code: int, payload: dict) -> httpx.Response:
    request = httpx.Request("GET", "https://example.test")
    return httpx.Response(status_code=status_code, request=request, json=payload)


def test_binance_signature_is_stable():
    signature = GatewayService._build_binance_signature("abc", "timestamp=123")
    assert signature == "0b2f5b440ad973f6bf2a2402d881d27d4cfc83636e59a4355ff3c55b50c9580c"


def test_okx_signature_is_stable():
    signature = GatewayService._build_okx_signature(
        "secret",
        "2020-12-08T09:08:57.715Z",
        "GET",
        "/api/v5/account/balance",
        "",
    )
    assert signature == "5ktoTKif8DCJlIPb/3Kfd1A17bIRye6jpS9QBWj+9AU="


def test_okx_requires_passphrase():
    service = GatewayService()
    account = _build_account("okx", is_testnet=True)
    result = service.validate_account(account, "key", "secret", None)
    assert not result.validated
    assert "passphrase" in result.message.lower()


def test_lighter_requires_numeric_account_index():
    service = GatewayService()
    account = _build_account("lighter")
    result = service.validate_account(account, "not-a-number", "auth-token", None)
    assert not result.validated
    assert "account_index" in result.message


def test_lighter_validate_success(monkeypatch):
    service = GatewayService()
    account = _build_account("lighter", is_testnet=True)

    def fake_lighter_request(base_url, method, path, params=None, headers=None, data=None, files=None):
        if path == "/api/v1/account":
            return _response(
                200,
                {
                    "code": 200,
                    "accounts": [{"account_index": 12, "positions": [], "assets": []}],
                },
            )
        if path == "/api/v1/trades":
            return _response(200, {"code": 200, "trades": []})
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(service, "_lighter_request", fake_lighter_request)
    result = service.validate_account(account, "12", "auth-token", None)
    assert result.validated
    assert "account=12" in result.message


def test_lighter_validate_rejects_payload_without_matching_account_index(monkeypatch):
    service = GatewayService()
    account = _build_account("lighter", is_testnet=True)

    def fake_lighter_request(base_url, method, path, params=None, headers=None, data=None, files=None):
        if path == "/api/v1/account":
            return _response(
                200,
                {
                    "code": 200,
                    "accounts": [{"account_index": 99, "positions": [], "assets": []}],
                },
            )
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(service, "_lighter_request", fake_lighter_request)
    result = service.validate_account(account, "12", "auth-token", None)
    assert result.validated is False
    assert "not found" in result.message.lower()


def test_lighter_fetch_state_maps_assets_positions_orders_trades(monkeypatch):
    service = GatewayService()
    account = _build_account("lighter", is_testnet=True)

    def fake_lighter_request(base_url, method, path, params=None, headers=None, data=None, files=None):
        if path == "/api/v1/account":
            return _response(
                200,
                {
                    "code": 200,
                    "accounts": [
                        {
                            "account_index": 12,
                            "pending_order_count": 1,
                            "assets": [{"symbol": "USDC", "balance": "1000", "locked_balance": "100"}],
                            "positions": [
                                {
                                    "market_id": 7,
                                    "symbol": "ETH-USDC",
                                    "sign": -1,
                                    "position": "0.25",
                                    "avg_entry_price": "2500",
                                    "unrealized_pnl": "3.5",
                                }
                            ],
                        }
                    ],
                },
            )
        if path == "/api/v1/orderBooks":
            return _response(200, {"code": 200, "order_books": [{"market_id": 7, "symbol": "ETH-USDC"}]})
        if path == "/api/v1/accountActiveOrders":
            return _response(
                200,
                {
                    "code": 200,
                    "orders": [
                        {
                            "market_index": 7,
                            "order_id": "9001",
                            "client_order_index": 42,
                            "status": "open",
                            "is_ask": True,
                            "type": "limit",
                            "price": "2490",
                            "initial_base_amount": "0.1",
                            "filled_base_amount": "0.02",
                            "filled_quote_amount": "49.8",
                        }
                    ],
                },
            )
        if path == "/api/v1/trades":
            return _response(
                200,
                {
                    "code": 200,
                    "trades": [
                        {
                            "trade_id": 555,
                            "market_id": 7,
                            "ask_account_id": 12,
                            "bid_account_id": 99,
                            "is_maker_ask": True,
                            "ask_id": 9001,
                            "price": "2491",
                            "size": "0.02",
                            "usd_amount": "49.82",
                            "maker_fee": "0.01",
                            "taker_fee": "0.02",
                            "timestamp": 1710000000,
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(service, "_lighter_request", fake_lighter_request)
    result = service.fetch_account_state(account, "12", "auth-token", None)
    assert result.success
    assert len(result.balances) == 1
    assert result.balances[0]["asset"] == "USDC"
    assert len(result.positions) == 1
    assert result.positions[0]["side"] == "SHORT"
    assert len(result.orders) == 1
    assert result.orders[0]["symbol"] == "ETH-USDC"
    assert len(result.trades) == 1
    assert result.trades[0]["side"] == "SELL"


def test_lighter_fetch_state_paginates_trades_with_next_cursor(monkeypatch):
    service = GatewayService()
    account = _build_account("lighter", is_testnet=True)
    trade_calls: list[dict] = []

    def fake_lighter_request(base_url, method, path, params=None, headers=None, data=None, files=None):
        if path == "/api/v1/account":
            return _response(
                200,
                {
                    "code": 200,
                    "accounts": [{"account_index": 12, "pending_order_count": 0, "assets": [], "positions": []}],
                },
            )
        if path == "/api/v1/orderBooks":
            return _response(200, {"code": 200, "order_books": [{"market_id": 7, "symbol": "ETH-USDC"}]})
        if path == "/api/v1/trades":
            trade_calls.append(dict(params or {}))
            if len(trade_calls) == 1:
                return _response(
                    200,
                    {
                        "code": 200,
                        "next_cursor": "cursor-2",
                        "trades": [
                            {
                                "trade_id": 200,
                                "market_id": 7,
                                "ask_account_id": 99,
                                "bid_account_id": 12,
                                "is_maker_ask": False,
                                "bid_id": 88,
                                "price": "2500",
                                "size": "0.01",
                                "usd_amount": "25",
                                "maker_fee": "0.01",
                                "taker_fee": "0.02",
                                "timestamp": 1710001000000,
                            }
                        ],
                    },
                )
            return _response(
                200,
                {
                    "code": 200,
                    "trades": [
                        {
                            "trade_id": 200,
                            "market_id": 7,
                            "ask_account_id": 99,
                            "bid_account_id": 12,
                            "is_maker_ask": False,
                            "bid_id": 88,
                            "price": "2500",
                            "size": "0.01",
                            "usd_amount": "25",
                            "maker_fee": "0.01",
                            "taker_fee": "0.02",
                            "timestamp": 1710001000000,
                        },
                        {
                            "trade_id": 199,
                            "market_id": 7,
                            "ask_account_id": 12,
                            "bid_account_id": 101,
                            "is_maker_ask": True,
                            "ask_id": 89,
                            "price": "2499",
                            "size": "0.01",
                            "usd_amount": "24.99",
                            "maker_fee": "0.01",
                            "taker_fee": "0.02",
                            "timestamp": 1710000900000,
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(service, "_lighter_request", fake_lighter_request)
    result = service.fetch_account_state(account, "12", "auth-token", None)
    assert result.success
    assert len(result.trades) == 2
    assert len(trade_calls) == 2
    assert trade_calls[1]["cursor"] == "cursor-2"


def test_fetch_binance_state_uses_trade_cursor_symbols_and_start_time(monkeypatch):
    service = GatewayService()
    account = _build_account("binance", is_testnet=True)
    trade_calls: list[dict] = []

    def fake_binance_request(base_url, method, path, api_key, api_secret, params=None):
        if path == "/api/v3/account":
            return _response(200, {"balances": []})
        if path == "/api/v3/openOrders":
            return _response(200, [])
        if path == "/fapi/v2/positionRisk":
            return _response(200, [])
        if path == "/api/v3/myTrades":
            trade_calls.append(dict(params or {}))
            return _response(200, [])
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(service, "_binance_signed_request", fake_binance_request)
    result = service.fetch_account_state(
        account,
        "key",
        "secret",
        None,
        trade_cursors={"symbols": {"btcusdt": {"last_trade_time_ms": 1710000000000}}},
    )
    assert result.success
    assert len(trade_calls) == 1
    assert trade_calls[0]["symbol"] == "BTCUSDT"
    assert trade_calls[0]["startTime"] == 1709999999000


def test_fetch_binance_state_bootstraps_symbols_from_balances(monkeypatch):
    service = GatewayService()
    account = _build_account("binance", is_testnet=True)
    trade_calls: list[dict] = []

    def fake_binance_request(base_url, method, path, api_key, api_secret, params=None):
        if path == "/api/v3/account":
            return _response(200, {"balances": [{"asset": "DOGE", "free": "25", "locked": "0"}]})
        if path == "/api/v3/openOrders":
            return _response(200, [])
        if path == "/fapi/v2/positionRisk":
            return _response(200, [])
        if path == "/api/v3/myTrades":
            trade_calls.append(dict(params or {}))
            return _response(200, [])
        raise AssertionError(f"unexpected path: {path}")

    def fake_binance_public_request(base_url, method, path, params=None):
        if path == "/api/v3/exchangeInfo":
            return _response(200, {"symbols": [{"symbol": "DOGEUSDT", "permissions": ["SPOT"]}]})
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(service, "_binance_signed_request", fake_binance_request)
    monkeypatch.setattr(service, "_binance_public_request", fake_binance_public_request)
    result = service.fetch_account_state(account, "key", "secret", None, trade_cursors=None)
    assert result.success
    assert len(trade_calls) == 1
    assert trade_calls[0]["symbol"] == "DOGEUSDT"
    assert "bootstrap" not in result.message.lower()


def test_fetch_binance_state_reports_warning_when_bootstrap_symbols_unavailable(monkeypatch):
    service = GatewayService()
    account = _build_account("binance", is_testnet=True)

    def fake_binance_request(base_url, method, path, api_key, api_secret, params=None):
        if path == "/api/v3/account":
            return _response(200, {"balances": [{"asset": "USDT", "free": "100", "locked": "0"}]})
        if path == "/api/v3/openOrders":
            return _response(200, [])
        if path == "/fapi/v2/positionRisk":
            return _response(200, [])
        if path == "/api/v3/myTrades":
            raise AssertionError("myTrades should not be called without symbol candidates")
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(service, "_binance_signed_request", fake_binance_request)
    monkeypatch.setattr(service, "_binance_public_request", lambda *args, **kwargs: _response(200, {"symbols": []}))
    result = service.fetch_account_state(account, "key", "secret", None, trade_cursors=None)
    assert result.success
    assert "trade bootstrap symbols unavailable" in result.message.lower()


def test_fetch_okx_state_applies_global_trade_cursor_to_fills(monkeypatch):
    service = GatewayService()
    account = _build_account("okx", is_testnet=True)
    fill_calls: list[dict] = []

    def fake_okx_request(**kwargs):
        path = kwargs["path"]
        if path == "/api/v5/account/balance":
            return _response(200, {"code": "0", "data": []})
        if path == "/api/v5/account/positions":
            return _response(200, {"code": "0", "data": []})
        if path == "/api/v5/trade/orders-pending":
            return _response(200, {"code": "0", "data": []})
        if path == "/api/v5/trade/fills":
            fill_calls.append(dict(kwargs.get("params") or {}))
            return _response(200, {"code": "0", "data": []})
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(service, "_okx_signed_request", fake_okx_request)
    result = service.fetch_account_state(
        account,
        "key",
        "secret",
        "passphrase",
        trade_cursors={"global": {"last_trade_time_ms": 1710000000000}},
    )
    assert result.success
    assert len(fill_calls) == 5
    assert all(item["begin"] == 1709999999000 for item in fill_calls)


def test_lighter_trade_params_include_cursor_window():
    params = GatewayService._build_lighter_trade_params(
        12,
        trade_cursors={"global": {"last_trade_time_ms": 1710000000000}},
    )
    assert params["account_index"] == 12
    assert params["timestamp_from"] == 1709999940000
    assert params["start_time"] == 1709999940000
    assert params["limit"] == 200


def test_lighter_trade_params_use_recent_window_without_cursor():
    params = GatewayService._build_lighter_trade_params(12, trade_cursors=None)
    assert params["account_index"] == 12
    assert params["limit"] == 200
    assert "timestamp_from" in params
    assert "start_time" in params
    assert params["timestamp_from"] == params["start_time"]
    assert params["timestamp_from"] > 0


def test_normalize_binance_balances_filters_zero_assets():
    payload = {
        "balances": [
            {"asset": "USDT", "free": "12.5", "locked": "0.5"},
            {"asset": "BTC", "free": "0", "locked": "0"},
        ]
    }
    balances = GatewayService._normalize_binance_balances(payload)
    assert len(balances) == 1
    assert balances[0]["asset"] == "USDT"
    assert balances[0]["total"] == 13.0


def test_normalize_okx_positions_maps_side():
    payload = [
        {"instId": "BTC-USDT-SWAP", "pos": "-2", "avgPx": "101", "markPx": "102", "upl": "2.5"},
        {"instId": "ETH-USDT-SWAP", "pos": "0", "avgPx": "0", "markPx": "0", "upl": "0"},
    ]
    positions = GatewayService._normalize_okx_positions(payload)
    assert len(positions) == 1
    assert positions[0]["symbol"] == "BTC-USDT-SWAP"
    assert positions[0]["side"] == "SHORT"
    assert positions[0]["quantity"] == 2.0


def test_normalize_binance_trades_maps_side_and_fee():
    payload = [
        {
            "symbol": "BTCUSDT",
            "id": 9988,
            "orderId": 8811,
            "price": "101.5",
            "qty": "0.02",
            "quoteQty": "2.03",
            "commission": "0.0002",
            "commissionAsset": "BNB",
            "time": 1710000000000,
            "isBuyer": True,
            "isMaker": False,
        }
    ]
    trades = GatewayService._normalize_binance_trades(payload)
    assert len(trades) == 1
    assert trades[0]["side"] == "BUY"
    assert trades[0]["fee_asset"] == "BNB"
    assert trades[0]["trade_id"] == "9988"


def test_normalize_binance_fills_from_order_creates_trade_rows():
    payload = {
        "symbol": "BTCUSDT",
        "orderId": 123,
        "side": "BUY",
        "transactTime": 1710000000000,
        "fills": [
            {"price": "100", "qty": "0.01", "commission": "0.0001", "commissionAsset": "BNB", "tradeId": 1},
            {"price": "101", "qty": "0.02", "commission": "0.0002", "commissionAsset": "BNB", "tradeId": 2},
        ],
    }
    fills = GatewayService._normalize_binance_fills_from_order(payload)
    assert len(fills) == 2
    assert fills[0]["trade_id"] == "1"
    assert fills[1]["quote_quantity"] == 2.02


def test_normalize_okx_trades_maps_fields():
    payload = [
        {
            "instId": "BTC-USDT-SWAP",
            "ordId": "8899",
            "tradeId": "7788",
            "side": "sell",
            "fillPx": "50000",
            "fillSz": "0.01",
            "fee": "-0.05",
            "feeCcy": "USDT",
            "execType": "M",
            "ts": "1710000000000",
        }
    ]
    trades = GatewayService._normalize_okx_trades(payload)
    assert len(trades) == 1
    assert trades[0]["symbol"] == "BTC-USDT-SWAP"
    assert trades[0]["side"] == "SELL"
    assert trades[0]["is_maker"] is True
    assert trades[0]["fee"] == 0.05


def test_place_binance_limit_order_success(monkeypatch):
    service = GatewayService()
    account = _build_account("binance", is_testnet=True)

    def fake_signed_request(base_url, method, path, api_key, api_secret, params=None):
        assert method == "POST"
        assert path == "/api/v3/order"
        return _response(
            200,
            {
                "symbol": "BTCUSDT",
                "orderId": 12345,
                "clientOrderId": "a1phquest-1",
                "status": "NEW",
                "side": "BUY",
                "type": "LIMIT",
                "price": "42000",
                "origQty": "0.01",
                "executedQty": "0",
                "cummulativeQuoteQty": "0",
            },
        )

    monkeypatch.setattr(service, "_binance_signed_request", fake_signed_request)

    result = service.place_order(
        account,
        api_key="key",
        api_secret="secret",
        passphrase=None,
        payload={
            "symbol": "BTCUSDT",
            "side": "BUY",
            "order_type": "LIMIT",
            "quantity": 0.01,
            "price": 42000,
            "time_in_force": "GTC",
            "client_order_id": "a1phquest-1",
        },
    )
    assert result.success
    assert result.order
    assert result.order["order_id"] == "12345"
    assert result.order["status"] == "NEW"


def test_place_binance_limit_requires_price():
    service = GatewayService()
    account = _build_account("binance", is_testnet=True)

    result = service.place_order(
        account,
        api_key="key",
        api_secret="secret",
        passphrase=None,
        payload={
            "symbol": "BTCUSDT",
            "side": "BUY",
            "order_type": "LIMIT",
            "quantity": 0.01,
        },
    )
    assert not result.success
    assert "requires price" in result.message.lower()


def test_place_lighter_requires_signed_tx_payload():
    service = GatewayService()
    account = _build_account("lighter", is_testnet=True)

    result = service.place_order(
        account,
        api_key="12",
        api_secret="auth-token",
        passphrase=None,
        payload={
            "symbol": "ETH-USDC",
            "side": "BUY",
            "order_type": "LIMIT",
            "quantity": 0.1,
            "price": 2500,
        },
    )
    assert not result.success
    assert "signed tx" in result.message.lower()


def test_place_lighter_order_success(monkeypatch):
    service = GatewayService()
    account = _build_account("lighter", is_testnet=True)

    def fake_send_tx(*, base_url, tx_type, tx_info, price_protection):
        return LighterSendTxResult(success=True, message="ok", payload={"code": 200, "tx_hash": "0xtxhash"})

    monkeypatch.setattr(service, "_lighter_send_tx", fake_send_tx)
    result = service.place_order(
        account,
        api_key="12",
        api_secret="auth-token",
        passphrase=None,
        payload={
            "symbol": "ETH-USDC",
            "side": "BUY",
            "order_type": "LIMIT",
            "quantity": 0.1,
            "price": 2500,
            "exchange_payload": {"tx_type": 1, "tx_info": "{\"MarketIndex\":7}"},
        },
    )
    assert result.success
    assert result.order
    assert result.order["order_id"] == "0xtxhash"
    assert result.order["status"] == "SUBMITTED"


def test_cancel_lighter_order_success(monkeypatch):
    service = GatewayService()
    account = _build_account("lighter", is_testnet=True)

    def fake_send_tx(*, base_url, tx_type, tx_info, price_protection):
        return LighterSendTxResult(success=True, message="ok", payload={"code": 200, "tx_hash": "0xcancelhash"})

    monkeypatch.setattr(service, "_lighter_send_tx", fake_send_tx)
    result = service.cancel_order(
        account,
        api_key="12",
        api_secret="auth-token",
        passphrase=None,
        order_id="9001",
        symbol="ETH-USDC",
        client_order_id="c-1",
        exchange_payload={"tx_type": 2, "tx_info": "{\"OrderIndex\":9001}"},
    )
    assert result.success
    assert result.order
    assert result.order["order_id"] == "9001"
    assert result.order["status"] == "CANCEL_SUBMITTED"


def test_cancel_okx_order_success(monkeypatch):
    service = GatewayService()
    account = _build_account("okx", is_testnet=True)

    def fake_okx_request(**kwargs):
        assert kwargs["method"] == "POST"
        assert kwargs["path"] == "/api/v5/trade/cancel-order"
        return _response(200, {"code": "0", "data": [{"ordId": "778899", "sCode": "0"}]})

    monkeypatch.setattr(service, "_okx_signed_request", fake_okx_request)

    result = service.cancel_order(
        account,
        api_key="key",
        api_secret="secret",
        passphrase="passphrase",
        order_id="778899",
        symbol="BTC-USDT-SWAP",
        client_order_id=None,
    )
    assert result.success
    assert result.order
    assert result.order["order_id"] == "778899"
    assert result.order["status"] == "CANCELED"
