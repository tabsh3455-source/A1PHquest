from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import Any
from urllib.parse import urlencode

import httpx

from ..config import get_settings
from ..models import ExchangeAccount

settings = get_settings()


@dataclass(slots=True)
class GatewayValidationResult:
    validated: bool
    message: str


@dataclass(slots=True)
class GatewaySyncResult:
    success: bool
    message: str
    balances: list[dict[str, Any]]
    positions: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    trades: list[dict[str, Any]]


@dataclass(slots=True)
class GatewayOrderResult:
    success: bool
    message: str
    order: dict[str, Any] | None = None


@dataclass(slots=True)
class LighterSendTxResult:
    success: bool
    message: str
    payload: dict[str, Any] | None


class GatewayService:
    """Gateway abstraction for Binance/OKX/Lighter account verification and execution."""

    def __init__(self) -> None:
        self.timeout = settings.gateway_validate_timeout_seconds

    def validate_account(
        self,
        account: ExchangeAccount,
        api_key: str,
        api_secret: str,
        passphrase: str | None = None,
    ) -> GatewayValidationResult:
        exchange = account.exchange.lower()
        if exchange == "binance":
            return self._validate_binance(api_key, api_secret, account.is_testnet)
        if exchange == "okx":
            return self._validate_okx(api_key, api_secret, passphrase, account.is_testnet)
        if exchange == "lighter":
            return self._validate_lighter(api_key, api_secret, account.is_testnet)
        return GatewayValidationResult(validated=False, message="Unsupported exchange")

    def fetch_account_state(
        self,
        account: ExchangeAccount,
        api_key: str,
        api_secret: str,
        passphrase: str | None = None,
        trade_cursors: dict[str, Any] | None = None,
    ) -> GatewaySyncResult:
        normalized_trade_cursors = _normalize_trade_cursors(trade_cursors)
        exchange = account.exchange.lower()
        if exchange == "binance":
            return self._fetch_binance_state(
                api_key,
                api_secret,
                account.is_testnet,
                trade_cursors=normalized_trade_cursors,
            )
        if exchange == "okx":
            return self._fetch_okx_state(
                api_key,
                api_secret,
                passphrase,
                account.is_testnet,
                trade_cursors=normalized_trade_cursors,
            )
        if exchange == "lighter":
            return self._fetch_lighter_state(
                api_key,
                api_secret,
                account.is_testnet,
                trade_cursors=normalized_trade_cursors,
            )
        return GatewaySyncResult(
            success=False,
            message=f"Exchange {exchange} sync is not available yet.",
            balances=[],
            positions=[],
            orders=[],
            trades=[],
        )

    def place_order(
        self,
        account: ExchangeAccount,
        api_key: str,
        api_secret: str,
        passphrase: str | None,
        payload: dict[str, Any],
    ) -> GatewayOrderResult:
        exchange = account.exchange.lower()
        if exchange == "binance":
            return self._place_binance_order(api_key, api_secret, account.is_testnet, payload)
        if exchange == "okx":
            return self._place_okx_order(api_key, api_secret, passphrase, account.is_testnet, payload)
        if exchange == "lighter":
            return self._place_lighter_order(api_key, api_secret, account.is_testnet, payload)
        return GatewayOrderResult(False, f"Exchange {exchange} order placement is not available yet.")

    def cancel_order(
        self,
        account: ExchangeAccount,
        api_key: str,
        api_secret: str,
        passphrase: str | None,
        *,
        order_id: str,
        symbol: str,
        client_order_id: str | None = None,
        exchange_payload: dict[str, Any] | None = None,
    ) -> GatewayOrderResult:
        exchange = account.exchange.lower()
        if exchange == "binance":
            return self._cancel_binance_order(
                api_key,
                api_secret,
                account.is_testnet,
                order_id=order_id,
                symbol=symbol,
                client_order_id=client_order_id,
            )
        if exchange == "okx":
            return self._cancel_okx_order(
                api_key,
                api_secret,
                passphrase,
                account.is_testnet,
                order_id=order_id,
                symbol=symbol,
                client_order_id=client_order_id,
            )
        if exchange == "lighter":
            return self._cancel_lighter_order(
                api_key,
                api_secret,
                account.is_testnet,
                order_id=order_id,
                symbol=symbol,
                client_order_id=client_order_id,
                exchange_payload=exchange_payload,
            )
        return GatewayOrderResult(False, f"Exchange {exchange} order cancel is not available yet.")

    def _validate_binance(self, api_key: str, api_secret: str, is_testnet: bool) -> GatewayValidationResult:
        if not api_key or not api_secret:
            return GatewayValidationResult(validated=False, message="Missing Binance key or secret.")

        base_url = settings.binance_testnet_base_url if is_testnet else settings.binance_spot_base_url
        response = self._binance_signed_request(base_url, "GET", "/api/v3/account", api_key, api_secret)
        if response.status_code == 200:
            payload = response.json()
            uid = payload.get("uid")
            msg = f"Binance credentials verified (uid={uid})" if uid else "Binance credentials verified"
            return GatewayValidationResult(validated=True, message=msg)

        return GatewayValidationResult(
            validated=False,
            message=f"Binance validation failed ({response.status_code}): {_extract_message(response)}",
        )

    def _validate_okx(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str | None,
        is_testnet: bool,
    ) -> GatewayValidationResult:
        if not api_key or not api_secret:
            return GatewayValidationResult(validated=False, message="Missing OKX key or secret.")
        if not passphrase:
            return GatewayValidationResult(validated=False, message="OKX passphrase is required.")

        response = self._okx_signed_request(
            method="GET",
            path="/api/v5/account/balance",
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            is_testnet=is_testnet,
        )
        if response.status_code == 200:
            payload = response.json()
            if payload.get("code") == "0":
                return GatewayValidationResult(validated=True, message="OKX credentials verified")
            return GatewayValidationResult(
                validated=False,
                message=f"OKX validation failed: {payload.get('msg', 'unknown error')}",
            )

        return GatewayValidationResult(
            validated=False,
            message=f"OKX validation failed ({response.status_code}): {_extract_message(response)}",
        )

    def _validate_lighter(self, api_key: str, api_secret: str, is_testnet: bool) -> GatewayValidationResult:
        # In our account model, `api_key` stores Lighter account_index.
        account_index = _to_int_or_none(api_key)
        if account_index is None or account_index < 0:
            return GatewayValidationResult(
                validated=False,
                message="Lighter api_key must be a valid account_index integer.",
            )

        base_url = settings.lighter_testnet_base_url if is_testnet else settings.lighter_base_url
        account_resp = self._lighter_request(
            base_url,
            "GET",
            "/api/v1/account",
            params={"by": "index", "value": str(account_index)},
        )
        if account_resp.status_code != 200:
            return GatewayValidationResult(
                validated=False,
                message=f"Lighter validation failed ({account_resp.status_code}): {_extract_message(account_resp)}",
            )

        payload = account_resp.json()
        if _to_int(payload.get("code")) not in {0, 200}:
            return GatewayValidationResult(
                validated=False,
                message=f"Lighter validation failed: {payload.get('message') or payload}",
            )

        account_row = self._extract_lighter_account(payload, account_index)
        if not account_row:
            return GatewayValidationResult(
                validated=False,
                message=f"Lighter account {account_index} not found.",
            )

        # auth token is optional in current API, but if provided we perform a private-trade probe.
        # This catches invalid auth values early during account validation.
        auth_value = (api_secret or "").strip()
        if auth_value:
            trades_resp = self._lighter_request(
                base_url,
                "GET",
                "/api/v1/trades",
                params={
                    "account_index": account_index,
                    "sort_by": "trade_id",
                    "sort_dir": "desc",
                    "limit": 1,
                    "auth": auth_value,
                },
                headers={"Authorization": f"Bearer {auth_value}"},
            )
            if trades_resp.status_code != 200:
                return GatewayValidationResult(
                    validated=False,
                    message=f"Lighter auth probe failed ({trades_resp.status_code}): {_extract_message(trades_resp)}",
                )
            trades_payload = trades_resp.json()
            if _to_int(trades_payload.get("code")) not in {0, 200}:
                return GatewayValidationResult(
                    validated=False,
                    message=f"Lighter auth probe failed: {trades_payload.get('message') or trades_payload}",
                )

        return GatewayValidationResult(validated=True, message=f"Lighter credentials verified (account={account_index})")

    def _fetch_binance_state(
        self,
        api_key: str,
        api_secret: str,
        is_testnet: bool,
        *,
        trade_cursors: dict[str, Any] | None = None,
    ) -> GatewaySyncResult:
        if not api_key or not api_secret:
            return GatewaySyncResult(False, "Missing Binance key or secret.", [], [], [], [])

        spot_base = settings.binance_testnet_base_url if is_testnet else settings.binance_spot_base_url
        futures_base = (
            settings.binance_futures_testnet_base_url if is_testnet else settings.binance_futures_base_url
        )
        warnings: list[str] = []

        account_resp = self._binance_signed_request(spot_base, "GET", "/api/v3/account", api_key, api_secret)
        if account_resp.status_code != 200:
            return GatewaySyncResult(
                False,
                f"Binance account sync failed: {_extract_message(account_resp)}",
                [],
                [],
                [],
                [],
            )

        account_payload = account_resp.json()
        balances = self._normalize_binance_balances(account_payload)

        orders: list[dict[str, Any]] = []
        orders_resp = self._binance_signed_request(spot_base, "GET", "/api/v3/openOrders", api_key, api_secret)
        if orders_resp.status_code == 200:
            orders = self._normalize_binance_orders(orders_resp.json())
        else:
            warnings.append(f"openOrders: {_extract_message(orders_resp)}")

        positions: list[dict[str, Any]] = []
        positions_resp = self._binance_signed_request(
            futures_base,
            "GET",
            "/fapi/v2/positionRisk",
            api_key,
            api_secret,
        )
        if positions_resp.status_code == 200:
            positions = self._normalize_binance_positions(positions_resp.json())
        else:
            warnings.append(f"positionRisk: {_extract_message(positions_resp)}")

        trades: list[dict[str, Any]] = []
        cursor_symbols = _extract_trade_cursor_symbols(trade_cursors)
        symbol_candidates = {
            str(item.get("symbol", "")).upper()
            for item in (orders + positions)
            if str(item.get("symbol", "")).strip()
        }
        symbol_candidates.update(cursor_symbols.keys())
        bootstrap_symbols = self._build_binance_trade_bootstrap_symbols(
            spot_base=spot_base,
            balances=balances,
            existing_symbols=symbol_candidates,
        )
        symbol_candidates.update(bootstrap_symbols)
        if not symbol_candidates and not cursor_symbols:
            warnings.append(
                "trade bootstrap symbols unavailable: initial Binance trade history may be incomplete until local symbol hints exist"
            )
        for symbol in sorted(symbol_candidates):
            # Pull incremental trade window when cursor exists; duplicates are deduped by DB unique key.
            cursor = cursor_symbols.get(symbol, {})
            params: dict[str, Any] = {"symbol": symbol, "limit": 200}
            since_ms = _to_int(cursor.get("last_trade_time_ms"), default=0)
            if since_ms > 0:
                params["startTime"] = max(since_ms - 1000, 0)
            trades_resp = self._binance_signed_request(
                spot_base,
                "GET",
                "/api/v3/myTrades",
                api_key,
                api_secret,
                params=params,
            )
            if trades_resp.status_code == 200:
                trades.extend(self._normalize_binance_trades(trades_resp.json()))
            else:
                warnings.append(f"myTrades {symbol}: {_extract_message(trades_resp)}")

        message = "Binance sync completed"
        if warnings:
            message = f"{message} with warnings: {'; '.join(warnings)}"
        return GatewaySyncResult(True, message, balances, positions, orders, trades)

    def _fetch_okx_state(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str | None,
        is_testnet: bool,
        *,
        trade_cursors: dict[str, Any] | None = None,
    ) -> GatewaySyncResult:
        if not api_key or not api_secret:
            return GatewaySyncResult(False, "Missing OKX key or secret.", [], [], [], [])
        if not passphrase:
            return GatewaySyncResult(False, "OKX passphrase is required.", [], [], [], [])

        warnings: list[str] = []
        balances_resp = self._okx_signed_request(
            method="GET",
            path="/api/v5/account/balance",
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            is_testnet=is_testnet,
        )
        if balances_resp.status_code != 200:
            return GatewaySyncResult(
                False,
                f"OKX balance sync failed: {_extract_message(balances_resp)}",
                [],
                [],
                [],
                [],
            )
        balances_payload = balances_resp.json()
        if balances_payload.get("code") != "0":
            return GatewaySyncResult(
                False,
                f"OKX balance sync failed: {balances_payload.get('msg', 'unknown error')}",
                [],
                [],
                [],
                [],
            )
        balances = self._normalize_okx_balances(balances_payload.get("data", []))

        positions: list[dict[str, Any]] = []
        positions_resp = self._okx_signed_request(
            method="GET",
            path="/api/v5/account/positions",
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            is_testnet=is_testnet,
        )
        if positions_resp.status_code == 200:
            positions_payload = positions_resp.json()
            if positions_payload.get("code") == "0":
                positions = self._normalize_okx_positions(positions_payload.get("data", []))
            else:
                warnings.append(f"positions: {positions_payload.get('msg', 'unknown error')}")
        else:
            warnings.append(f"positions: {_extract_message(positions_resp)}")

        orders: list[dict[str, Any]] = []
        trades: list[dict[str, Any]] = []
        global_since_ms = _extract_global_trade_cursor_ms(trade_cursors)
        for inst_type in ("SPOT", "MARGIN", "SWAP", "FUTURES", "OPTION"):
            orders_resp = self._okx_signed_request(
                method="GET",
                path="/api/v5/trade/orders-pending",
                params={"instType": inst_type},
                api_key=api_key,
                api_secret=api_secret,
                passphrase=passphrase,
                is_testnet=is_testnet,
            )
            if orders_resp.status_code == 200:
                orders_payload = orders_resp.json()
                if orders_payload.get("code") == "0":
                    orders.extend(self._normalize_okx_orders(orders_payload.get("data", [])))
                else:
                    warnings.append(f"orders-pending {inst_type}: {orders_payload.get('msg', 'unknown error')}")
            else:
                warnings.append(f"orders-pending {inst_type}: {_extract_message(orders_resp)}")

            fills_params: dict[str, Any] = {"instType": inst_type, "limit": 100}
            if global_since_ms > 0:
                # OKX returns fills sorted by time; begin narrows incremental sync window.
                fills_params["begin"] = max(global_since_ms - 1000, 0)
            fills_resp = self._okx_signed_request(
                method="GET",
                path="/api/v5/trade/fills",
                params=fills_params,
                api_key=api_key,
                api_secret=api_secret,
                passphrase=passphrase,
                is_testnet=is_testnet,
            )
            if fills_resp.status_code == 200:
                fills_payload = fills_resp.json()
                if fills_payload.get("code") == "0":
                    trades.extend(self._normalize_okx_trades(fills_payload.get("data", [])))
                else:
                    warnings.append(f"fills {inst_type}: {fills_payload.get('msg', 'unknown error')}")
            else:
                warnings.append(f"fills {inst_type}: {_extract_message(fills_resp)}")

        message = "OKX sync completed"
        if warnings:
            message = f"{message} with warnings: {'; '.join(warnings)}"
        return GatewaySyncResult(True, message, balances, positions, orders, trades)

    def _fetch_lighter_state(
        self,
        api_key: str,
        api_secret: str,
        is_testnet: bool,
        *,
        trade_cursors: dict[str, Any] | None = None,
    ) -> GatewaySyncResult:
        # In our account model, `api_key` stores Lighter account_index.
        account_index = _to_int_or_none(api_key)
        if account_index is None or account_index < 0:
            return GatewaySyncResult(
                False,
                "Lighter api_key must be a valid account_index integer.",
                [],
                [],
                [],
                [],
            )

        base_url = settings.lighter_testnet_base_url if is_testnet else settings.lighter_base_url
        warnings: list[str] = []
        account_resp = self._lighter_request(
            base_url,
            "GET",
            "/api/v1/account",
            params={"by": "index", "value": str(account_index)},
        )
        if account_resp.status_code != 200:
            return GatewaySyncResult(
                False,
                f"Lighter account sync failed ({account_resp.status_code}): {_extract_message(account_resp)}",
                [],
                [],
                [],
                [],
            )

        account_payload = account_resp.json()
        if _to_int(account_payload.get("code")) not in {0, 200}:
            return GatewaySyncResult(
                False,
                f"Lighter account sync failed: {account_payload.get('message') or account_payload}",
                [],
                [],
                [],
                [],
            )

        account_row = self._extract_lighter_account(account_payload, account_index)
        if not account_row:
            return GatewaySyncResult(
                False,
                f"Lighter account {account_index} not found.",
                [],
                [],
                [],
                [],
            )

        # Build market_id -> symbol map once so position/order/trade payloads are readable.
        market_symbols = self._fetch_lighter_market_symbols(base_url, warnings)
        balances = self._normalize_lighter_balances(account_row)
        positions = self._normalize_lighter_positions(account_row, market_symbols)

        auth_value = (api_secret or "").strip()
        orders: list[dict[str, Any]] = []
        market_candidates = {
            _to_int(pos.get("market_id"))
            for pos in account_row.get("positions", [])
            if _to_int(pos.get("market_id")) >= 0
        }

        # Lighter accountActiveOrders requires market_id; when we don't have position hints but pending orders exist,
        # we probe 255 as an all-market fallback.
        if not market_candidates and _to_int(account_row.get("pending_order_count")) > 0:
            market_candidates.add(255)

        for market_id in sorted(market_candidates):
            order_params: dict[str, Any] = {
                "account_index": account_index,
                "market_id": market_id,
            }
            if auth_value:
                order_params["auth"] = auth_value
            orders_resp = self._lighter_request(
                base_url,
                "GET",
                "/api/v1/accountActiveOrders",
                params=order_params,
                headers={"Authorization": f"Bearer {auth_value}"} if auth_value else None,
            )
            if orders_resp.status_code != 200:
                warnings.append(f"accountActiveOrders {market_id}: {_extract_message(orders_resp)}")
                continue

            orders_payload = orders_resp.json()
            if _to_int(orders_payload.get("code")) not in {0, 200}:
                warnings.append(f"accountActiveOrders {market_id}: {orders_payload.get('message') or orders_payload}")
                continue
            orders.extend(self._normalize_lighter_orders(orders_payload.get("orders", []), market_symbols))

        trades = self._fetch_lighter_trades(
            base_url=base_url,
            account_index=account_index,
            market_symbols=market_symbols,
            auth_value=auth_value,
            trade_cursors=trade_cursors,
            warnings=warnings,
        )

        message = "Lighter sync completed"
        if warnings:
            message = f"{message} with warnings: {'; '.join(warnings)}"
        return GatewaySyncResult(True, message, balances, positions, orders, trades)

    @staticmethod
    def _build_lighter_trade_params(account_index: int, *, trade_cursors: dict[str, Any] | None) -> dict[str, Any]:
        """
        Build trade query params with cursor hints when available.

        Lighter pagination semantics may vary by API version, so we send multiple
        best-effort timestamp fields. Unsupported params are ignored by server while
        local upsert de-duplicates repeated trades.
        """
        params: dict[str, Any] = {
            "account_index": account_index,
            "sort_by": "trade_id",
            "sort_dir": "desc",
            "role": "all",
            "type": "all",
            "limit": 200,
        }
        cursor_ms = _extract_global_trade_cursor_ms(trade_cursors)
        if cursor_ms > 0:
            window_start = max(cursor_ms - 60_000, 0)
        else:
            # Fallback when no cursor exists yet: query recent window so first sync
            # is bounded while still likely to include the latest executions.
            fallback_ms = max(int(settings.lighter_trade_fallback_window_seconds), 1) * 1000
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            window_start = max(now_ms - fallback_ms, 0)
        params["timestamp_from"] = window_start
        params["start_time"] = window_start
        return params

    def _fetch_lighter_trades(
        self,
        *,
        base_url: str,
        account_index: int,
        market_symbols: dict[int, str],
        auth_value: str,
        trade_cursors: dict[str, Any] | None,
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        """
        Fetch Lighter trades with best-effort pagination.

        The endpoint can expose different pagination semantics across versions
        (cursor token vs. has_more + time window), so we combine:
        - cursor-driven paging when token exists
        - fallback time/id hints when only `has_more` style flags are returned
        """
        params = self._build_lighter_trade_params(account_index, trade_cursors=trade_cursors)
        if auth_value:
            params["auth"] = auth_value
        headers = {"Authorization": f"Bearer {auth_value}"} if auth_value else None

        seen_page_signatures: set[str] = set()
        trades: list[dict[str, Any]] = []
        max_pages = max(int(settings.lighter_trade_sync_max_pages), 1)

        for page_index in range(max_pages):
            trades_resp = self._lighter_request(
                base_url,
                "GET",
                "/api/v1/trades",
                params=params,
                headers=headers,
            )
            if trades_resp.status_code != 200:
                warnings.append(f"trades: {_extract_message(trades_resp)}")
                break

            trades_payload = trades_resp.json()
            if _to_int(trades_payload.get("code")) not in {0, 200}:
                warnings.append(f"trades: {trades_payload.get('message') or trades_payload}")
                break

            raw_trades = trades_payload.get("trades", [])
            if not isinstance(raw_trades, list):
                warnings.append("trades: invalid payload shape, expected list")
                break
            if not raw_trades:
                break

            page_signature = _lighter_trade_page_signature(raw_trades)
            if page_signature in seen_page_signatures:
                warnings.append("trades: pagination repeated same page, stopped to avoid loop")
                break
            seen_page_signatures.add(page_signature)

            trades.extend(
                self._normalize_lighter_trades(
                    raw_trades,
                    account_index=account_index,
                    market_symbols=market_symbols,
                )
            )

            next_params = _build_lighter_next_trade_params(params, trades_payload, raw_trades)
            if not next_params:
                break
            params = next_params

            if page_index == max_pages - 1:
                warnings.append(f"trades: pagination reached max pages ({max_pages})")
        # Different pagination modes can overlap records across pages; dedupe here
        # so downstream reconciliation and audit counters stay stable.
        return _dedupe_lighter_trades(trades)

    def _fetch_lighter_market_symbols(self, base_url: str, warnings: list[str]) -> dict[int, str]:
        response = self._lighter_request(base_url, "GET", "/api/v1/orderBooks")
        if response.status_code != 200:
            warnings.append(f"orderBooks: {_extract_message(response)}")
            return {}

        payload = response.json()
        if _to_int(payload.get("code")) not in {0, 200}:
            warnings.append(f"orderBooks: {payload.get('message') or payload}")
            return {}

        result: dict[int, str] = {}
        for row in payload.get("order_books", []):
            market_id = _to_int(row.get("market_id"), default=-1)
            if market_id < 0:
                continue
            symbol = str(row.get("symbol", "")).upper()
            if symbol:
                result[market_id] = symbol
        return result

    def _place_binance_order(
        self,
        api_key: str,
        api_secret: str,
        is_testnet: bool,
        payload: dict[str, Any],
    ) -> GatewayOrderResult:
        base_url = settings.binance_testnet_base_url if is_testnet else settings.binance_spot_base_url
        symbol = str(payload["symbol"]).upper()
        side = str(payload["side"]).upper()
        order_type = str(payload["order_type"]).upper()
        quantity = _format_decimal(float(payload["quantity"]))
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
            "newOrderRespType": "FULL",
        }

        if order_type == "LIMIT":
            price = payload.get("price")
            if not price:
                return GatewayOrderResult(False, "LIMIT order requires price")
            params["price"] = _format_decimal(float(price))
            params["timeInForce"] = str(payload.get("time_in_force", "GTC"))
        if payload.get("client_order_id"):
            params["newClientOrderId"] = str(payload["client_order_id"])

        response = self._binance_signed_request(base_url, "POST", "/api/v3/order", api_key, api_secret, params=params)
        if response.status_code != 200:
            return GatewayOrderResult(
                False,
                f"Binance order placement failed ({response.status_code}): {_extract_message(response)}",
            )

        order_payload = response.json()
        order = self._normalize_binance_order(order_payload)
        fills = self._normalize_binance_fills_from_order(order_payload)
        if fills:
            order["trades"] = fills
        return GatewayOrderResult(True, "Binance order submitted", order)

    def _place_okx_order(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str | None,
        is_testnet: bool,
        payload: dict[str, Any],
    ) -> GatewayOrderResult:
        if not passphrase:
            return GatewayOrderResult(False, "OKX passphrase is required.")

        symbol = str(payload["symbol"]).upper()
        side = str(payload["side"]).lower()
        order_type = str(payload["order_type"]).lower()
        body: dict[str, Any] = {
            "instId": symbol,
            "tdMode": str(payload.get("td_mode", "cash")),
            "side": side,
            "ordType": order_type,
            "sz": _format_decimal(float(payload["quantity"])),
        }

        if order_type == "limit":
            price = payload.get("price")
            if not price:
                return GatewayOrderResult(False, "LIMIT order requires price")
            body["px"] = _format_decimal(float(price))
        if payload.get("client_order_id"):
            body["clOrdId"] = str(payload["client_order_id"])
        if payload.get("reduce_only"):
            body["reduceOnly"] = True

        response = self._okx_signed_request(
            method="POST",
            path="/api/v5/trade/order",
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            is_testnet=is_testnet,
            json_body=body,
        )
        if response.status_code != 200:
            return GatewayOrderResult(
                False,
                f"OKX order placement failed ({response.status_code}): {_extract_message(response)}",
            )

        payload_json = response.json()
        if payload_json.get("code") != "0":
            return GatewayOrderResult(False, f"OKX order placement failed: {payload_json.get('msg', 'unknown error')}")

        data = payload_json.get("data", [])
        if not data:
            return GatewayOrderResult(False, "OKX order placement returned empty result")
        first = data[0]
        if first.get("sCode") and first.get("sCode") != "0":
            return GatewayOrderResult(False, f"OKX order placement failed: {first.get('sMsg', 'unknown error')}")

        order = {
            "symbol": symbol,
            "order_id": str(first.get("ordId", "")),
            "client_order_id": first.get("clOrdId") or body.get("clOrdId"),
            "status": "LIVE",
            "side": side.upper(),
            "order_type": order_type.upper(),
            "price": float(payload.get("price") or 0),
            "quantity": float(payload["quantity"]),
            "filled_quantity": 0.0,
            "avg_fill_price": None,
            "raw": {"request": body, "response": payload_json},
        }
        fill_size = _to_float(first.get("fillSz"))
        if fill_size > 0:
            fill_price = _to_float(first.get("fillPx"))
            order["trades"] = [
                {
                    "symbol": symbol,
                    "order_id": order["order_id"],
                    "trade_id": str(first.get("tradeId") or f"{order['order_id']}-0"),
                    "side": side.upper(),
                    "price": fill_price,
                    "quantity": fill_size,
                    "quote_quantity": fill_price * fill_size,
                    "fee": _to_float(first.get("fee")),
                    "fee_asset": first.get("feeCcy"),
                    "is_maker": str(first.get("execType", "")).upper() == "M",
                    "trade_time": first.get("fillTime") or first.get("uTime"),
                    "raw": first,
                }
            ]
        return GatewayOrderResult(True, "OKX order submitted", order)

    def _place_lighter_order(
        self,
        api_key: str,
        api_secret: str,
        is_testnet: bool,
        payload: dict[str, Any],
    ) -> GatewayOrderResult:
        account_index = _to_int_or_none(api_key)
        if account_index is None or account_index < 0:
            return GatewayOrderResult(False, "Lighter api_key must be a valid account_index integer.")

        # Lighter order placement requires client-signed tx payload.
        # We accept this payload via `exchange_payload` to keep API compatibility.
        exchange_payload = _extract_lighter_exchange_payload(payload)
        tx_type = _to_int_or_none(exchange_payload.get("tx_type"))
        tx_info = str(exchange_payload.get("tx_info", "")).strip()
        if tx_type is None or not tx_info:
            return GatewayOrderResult(
                False,
                "Lighter order requires exchange_payload.tx_type and exchange_payload.tx_info (signed tx).",
            )

        send_result = self._lighter_send_tx(
            base_url=settings.lighter_testnet_base_url if is_testnet else settings.lighter_base_url,
            tx_type=tx_type,
            tx_info=tx_info,
            price_protection=exchange_payload.get("price_protection"),
        )
        if not send_result.success or not send_result.payload:
            return GatewayOrderResult(False, send_result.message)

        send_payload = send_result.payload
        tx_info_dict = _parse_json_dict(tx_info)
        market_id = _to_int(
            _pick_dict_value(tx_info_dict, "MarketIndex", "market_index", "marketId", "market_id"),
            default=-1,
        )

        symbol = str(payload.get("symbol", "")).upper()
        if not symbol and market_id >= 0:
            market_symbols = self._fetch_lighter_market_symbols(
                settings.lighter_testnet_base_url if is_testnet else settings.lighter_base_url,
                warnings=[],
            )
            symbol = market_symbols.get(market_id, f"MARKET-{market_id}")

        side = str(payload.get("side", "")).upper()
        order_type = str(payload.get("order_type", "")).upper()
        tx_hash = str(send_payload.get("tx_hash") or "")
        order = {
            "symbol": symbol or "UNKNOWN",
            # Lighter returns tx hash immediately; exchange order index is available asynchronously.
            "order_id": tx_hash or str(_pick_dict_value(tx_info_dict, "OrderIndex", "order_index", "ClientOrderIndex", "client_order_index") or ""),
            "client_order_id": payload.get("client_order_id"),
            "status": "SUBMITTED",
            "side": side or "UNKNOWN",
            "order_type": order_type or "UNKNOWN",
            "price": _to_float(payload.get("price")),
            "quantity": _to_float(payload.get("quantity")),
            "filled_quantity": 0.0,
            "avg_fill_price": None,
            "raw": {
                "request": {
                    "tx_type": tx_type,
                    "tx_info": tx_info,
                    "price_protection": exchange_payload.get("price_protection"),
                },
                "response": send_payload,
            },
        }
        return GatewayOrderResult(True, "Lighter order submitted", order)

    def _cancel_lighter_order(
        self,
        api_key: str,
        api_secret: str,
        is_testnet: bool,
        *,
        order_id: str,
        symbol: str,
        client_order_id: str | None,
        exchange_payload: dict[str, Any] | None,
    ) -> GatewayOrderResult:
        account_index = _to_int_or_none(api_key)
        if account_index is None or account_index < 0:
            return GatewayOrderResult(False, "Lighter api_key must be a valid account_index integer.")

        payload = _extract_lighter_exchange_payload(exchange_payload or {})
        tx_type = _to_int_or_none(payload.get("tx_type"))
        tx_info = str(payload.get("tx_info", "")).strip()
        if tx_type is None or not tx_info:
            return GatewayOrderResult(
                False,
                "Lighter cancel requires exchange_payload.tx_type and exchange_payload.tx_info (signed cancel tx).",
            )

        send_result = self._lighter_send_tx(
            base_url=settings.lighter_testnet_base_url if is_testnet else settings.lighter_base_url,
            tx_type=tx_type,
            tx_info=tx_info,
            price_protection=payload.get("price_protection"),
        )
        if not send_result.success or not send_result.payload:
            return GatewayOrderResult(False, send_result.message)

        send_payload = send_result.payload
        normalized = {
            "symbol": symbol.upper(),
            "order_id": order_id or str(send_payload.get("tx_hash", "")),
            "client_order_id": client_order_id,
            "status": "CANCEL_SUBMITTED",
            "side": "",
            "order_type": "",
            "price": 0.0,
            "quantity": 0.0,
            "filled_quantity": 0.0,
            "avg_fill_price": None,
            "raw": {
                "request": {
                    "tx_type": tx_type,
                    "tx_info": tx_info,
                    "price_protection": payload.get("price_protection"),
                },
                "response": send_payload,
            },
        }
        return GatewayOrderResult(True, "Lighter cancel submitted", normalized)

    def _cancel_binance_order(
        self,
        api_key: str,
        api_secret: str,
        is_testnet: bool,
        *,
        order_id: str,
        symbol: str,
        client_order_id: str | None,
    ) -> GatewayOrderResult:
        base_url = settings.binance_testnet_base_url if is_testnet else settings.binance_spot_base_url
        params: dict[str, Any] = {"symbol": symbol.upper()}
        if order_id:
            params["orderId"] = order_id
        elif client_order_id:
            params["origClientOrderId"] = client_order_id
        else:
            return GatewayOrderResult(False, "order_id or client_order_id is required")

        response = self._binance_signed_request(base_url, "DELETE", "/api/v3/order", api_key, api_secret, params=params)
        if response.status_code != 200:
            return GatewayOrderResult(
                False,
                f"Binance cancel failed ({response.status_code}): {_extract_message(response)}",
            )
        order_payload = response.json()
        order = self._normalize_binance_order(order_payload)
        order["status"] = "CANCELED"
        return GatewayOrderResult(True, "Binance order canceled", order)

    def _cancel_okx_order(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str | None,
        is_testnet: bool,
        *,
        order_id: str,
        symbol: str,
        client_order_id: str | None,
    ) -> GatewayOrderResult:
        if not passphrase:
            return GatewayOrderResult(False, "OKX passphrase is required.")

        body: dict[str, Any] = {"instId": symbol.upper()}
        if order_id:
            body["ordId"] = order_id
        elif client_order_id:
            body["clOrdId"] = client_order_id
        else:
            return GatewayOrderResult(False, "order_id or client_order_id is required")

        response = self._okx_signed_request(
            method="POST",
            path="/api/v5/trade/cancel-order",
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            is_testnet=is_testnet,
            json_body=body,
        )
        if response.status_code != 200:
            return GatewayOrderResult(
                False,
                f"OKX cancel failed ({response.status_code}): {_extract_message(response)}",
            )
        payload_json = response.json()
        if payload_json.get("code") != "0":
            return GatewayOrderResult(False, f"OKX cancel failed: {payload_json.get('msg', 'unknown error')}")
        data = payload_json.get("data", [])
        if not data:
            return GatewayOrderResult(False, "OKX cancel returned empty result")
        first = data[0]
        if first.get("sCode") and first.get("sCode") != "0":
            return GatewayOrderResult(False, f"OKX cancel failed: {first.get('sMsg', 'unknown error')}")

        order = {
            "symbol": symbol.upper(),
            "order_id": str(first.get("ordId") or order_id),
            "client_order_id": first.get("clOrdId") or client_order_id,
            "status": "CANCELED",
            "side": "",
            "order_type": "",
            "price": 0.0,
            "quantity": 0.0,
            "filled_quantity": 0.0,
            "avg_fill_price": None,
            "raw": {"request": body, "response": payload_json},
        }
        return GatewayOrderResult(True, "OKX order canceled", order)

    def _lighter_request(
        self,
        base_url: str,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, tuple[str | None, str]] | None = None,
    ) -> httpx.Response:
        method_upper = method.upper()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                return client.request(
                    method_upper,
                    f"{base_url}{path}",
                    params=params,
                    headers=headers,
                    data=data,
                    files=files,
                )
        except httpx.RequestError as exc:
            return _synthetic_error_response(f"lighter network error: {exc}")

    def _lighter_send_tx(
        self,
        *,
        base_url: str,
        tx_type: int,
        tx_info: str,
        price_protection: Any | None,
    ) -> LighterSendTxResult:
        form: dict[str, tuple[str | None, str]] = {
            "tx_type": (None, str(tx_type)),
            "tx_info": (None, tx_info),
        }
        if price_protection is not None:
            form["price_protection"] = (None, "true" if bool(price_protection) else "false")

        response = self._lighter_request(
            base_url,
            "POST",
            "/api/v1/sendTx",
            files=form,
        )
        if response.status_code != 200:
            return LighterSendTxResult(
                success=False,
                message=f"Lighter sendTx failed ({response.status_code}): {_extract_message(response)}",
                payload=None,
            )

        payload = response.json()
        code = _to_int(payload.get("code"))
        if code not in {0, 200}:
            return LighterSendTxResult(
                success=False,
                message=f"Lighter sendTx failed: {payload.get('message') or payload}",
                payload=payload,
            )
        return LighterSendTxResult(success=True, message="ok", payload=payload)

    def _binance_signed_request(
        self,
        base_url: str,
        method: str,
        path: str,
        api_key: str,
        api_secret: str,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        final_params: dict[str, Any] = dict(params or {})
        final_params["timestamp"] = int(datetime.now(timezone.utc).timestamp() * 1000)
        query = urlencode(final_params, doseq=True)
        final_params["signature"] = self._build_binance_signature(api_secret, query)
        headers = {"X-MBX-APIKEY": api_key}
        method_upper = method.upper()

        try:
            with httpx.Client(timeout=self.timeout) as client:
                if method_upper in {"GET", "DELETE"}:
                    return client.request(method_upper, f"{base_url}{path}", params=final_params, headers=headers)
                return client.request(method_upper, f"{base_url}{path}", data=final_params, headers=headers)
        except httpx.RequestError as exc:
            return _synthetic_error_response(f"binance network error: {exc}")

    def _binance_public_request(
        self,
        base_url: str,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                return client.request(method.upper(), f"{base_url}{path}", params=params or {})
        except httpx.RequestError as exc:
            return _synthetic_error_response(f"binance network error: {exc}")

    def _build_binance_trade_bootstrap_symbols(
        self,
        *,
        spot_base: str,
        balances: list[dict[str, Any]],
        existing_symbols: set[str],
    ) -> set[str]:
        """
        Derive conservative spot trade bootstrap symbols from current balances.

        Binance `myTrades` requires an explicit symbol, so a brand-new account
        with no local cursors/orders can otherwise miss trade history entirely.
        We infer a bounded set of likely symbols from non-zero assets and then
        intersect with exchangeInfo so we do not query invalid markets.
        """
        inferred_candidates = self._build_binance_balance_symbol_candidates(
            balances,
            existing_symbols=existing_symbols,
        )
        if not inferred_candidates:
            return set()

        exchange_info_resp = self._binance_public_request(spot_base, "GET", "/api/v3/exchangeInfo")
        if exchange_info_resp.status_code != 200:
            return set()
        valid_symbols = self._normalize_binance_exchange_symbols(exchange_info_resp.json())
        if not valid_symbols:
            return set()
        return {symbol for symbol in inferred_candidates if symbol in valid_symbols}

    @staticmethod
    def _build_binance_balance_symbol_candidates(
        balances: list[dict[str, Any]],
        *,
        existing_symbols: set[str],
        max_symbols: int = 24,
    ) -> set[str]:
        common_quotes = ("USDT", "USDC", "FDUSD", "BUSD", "TUSD", "BTC", "ETH", "BNB")
        held_assets = [
            str(row.get("asset") or "").upper().strip()
            for row in balances
            if str(row.get("asset") or "").strip() and _to_float(row.get("total")) > 0
        ]
        if not held_assets:
            return set()

        candidates: list[str] = []
        seen: set[str] = set(existing_symbols)
        for asset in held_assets:
            if asset in common_quotes:
                continue
            for quote in common_quotes:
                if asset == quote:
                    continue
                symbol = f"{asset}{quote}"
                if symbol in seen:
                    continue
                seen.add(symbol)
                candidates.append(symbol)
                if len(candidates) >= max_symbols:
                    return set(candidates)
        return set(candidates)

    @staticmethod
    def _normalize_binance_exchange_symbols(payload: dict[str, Any]) -> set[str]:
        result: set[str] = set()
        for item in payload.get("symbols", []):
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            permissions = item.get("permissions")
            if isinstance(permissions, list) and permissions and "SPOT" not in permissions:
                continue
            result.add(symbol)
        return result

    def _okx_signed_request(
        self,
        *,
        method: str,
        path: str,
        api_key: str,
        api_secret: str,
        passphrase: str,
        is_testnet: bool,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        query = urlencode(params or {}, doseq=True)
        request_path = f"{path}?{query}" if query else path
        method_upper = method.upper()
        body_str = json.dumps(json_body or {}, separators=(",", ":")) if method_upper in {"POST", "PUT"} else ""
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        sign = self._build_okx_signature(api_secret, timestamp, method_upper, request_path, body_str)
        headers = {
            "OK-ACCESS-KEY": api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json",
        }
        if is_testnet:
            headers["x-simulated-trading"] = "1"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                if method_upper in {"POST", "PUT"}:
                    return client.request(method_upper, f"{settings.okx_base_url}{request_path}", headers=headers, content=body_str)
                return client.request(method_upper, f"{settings.okx_base_url}{request_path}", headers=headers)
        except httpx.RequestError as exc:
            return _synthetic_error_response(f"okx network error: {exc}")

    @staticmethod
    def _normalize_binance_order(item: dict[str, Any]) -> list[dict[str, Any]] | dict[str, Any]:
        executed = _to_float(item.get("executedQty"))
        cummulative_quote_qty = _to_float(item.get("cummulativeQuoteQty"))
        avg_fill_price: float | None = None
        if executed > 0 and cummulative_quote_qty > 0:
            avg_fill_price = cummulative_quote_qty / executed
        return {
            "symbol": str(item.get("symbol", "")),
            "order_id": str(item.get("orderId", "")),
            "client_order_id": item.get("clientOrderId"),
            "status": str(item.get("status", "")),
            "side": str(item.get("side", "")),
            "order_type": str(item.get("type", "")),
            "price": _to_float(item.get("price")),
            "quantity": _to_float(item.get("origQty")),
            "filled_quantity": executed,
            "avg_fill_price": avg_fill_price,
            "raw": item,
        }

    @staticmethod
    def _normalize_binance_balances(payload: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in payload.get("balances", []):
            free = _to_float(item.get("free"))
            locked = _to_float(item.get("locked"))
            total = free + locked
            if total <= 0:
                continue
            result.append(
                {
                    "asset": str(item.get("asset", "")),
                    "free": free,
                    "locked": locked,
                    "total": total,
                }
            )
        return result

    @staticmethod
    def _normalize_binance_positions(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in payload:
            qty = _to_float(item.get("positionAmt"))
            if qty == 0:
                continue
            side = "LONG" if qty > 0 else "SHORT"
            result.append(
                {
                    "symbol": str(item.get("symbol", "")),
                    "side": side,
                    "quantity": abs(qty),
                    "entry_price": _to_float(item.get("entryPrice")),
                    "mark_price": _to_float(item.get("markPrice")),
                    "unrealized_pnl": _to_float(item.get("unRealizedProfit")),
                }
            )
        return result

    @staticmethod
    def _normalize_binance_orders(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [GatewayService._normalize_binance_order(item) for item in payload]

    @staticmethod
    def _normalize_binance_trades(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in payload:
            side = "BUY" if bool(item.get("isBuyer")) else "SELL"
            price = _to_float(item.get("price"))
            quantity = _to_float(item.get("qty"))
            result.append(
                {
                    "symbol": str(item.get("symbol", "")).upper(),
                    "order_id": str(item.get("orderId", "")),
                    "trade_id": str(item.get("id", "")),
                    "side": side,
                    "price": price,
                    "quantity": quantity,
                    "quote_quantity": _to_float(item.get("quoteQty"), price * quantity),
                    "fee": _to_float(item.get("commission")),
                    "fee_asset": item.get("commissionAsset"),
                    "is_maker": bool(item.get("isMaker")),
                    "trade_time": item.get("time"),
                    "raw": item,
                }
            )
        return result

    @staticmethod
    def _normalize_binance_fills_from_order(order_payload: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        fills = order_payload.get("fills", [])
        if not fills:
            return result

        symbol = str(order_payload.get("symbol", "")).upper()
        order_id = str(order_payload.get("orderId", ""))
        side = str(order_payload.get("side", "")).upper()
        trade_time = order_payload.get("transactTime")

        for index, fill in enumerate(fills):
            price = _to_float(fill.get("price"))
            quantity = _to_float(fill.get("qty"))
            trade_id = str(fill.get("tradeId") or f"{order_id}-{index}")
            result.append(
                {
                    "symbol": symbol,
                    "order_id": order_id,
                    "trade_id": trade_id,
                    "side": side,
                    "price": price,
                    "quantity": quantity,
                    "quote_quantity": price * quantity,
                    "fee": _to_float(fill.get("commission")),
                    "fee_asset": fill.get("commissionAsset"),
                    "is_maker": bool(fill.get("isMaker", False)),
                    "trade_time": trade_time,
                    "raw": fill,
                }
            )
        return result

    @staticmethod
    def _normalize_okx_balances(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for account_bucket in payload:
            for item in account_bucket.get("details", []):
                asset = str(item.get("ccy", ""))
                free = _to_float(item.get("availBal")) or _to_float(item.get("cashBal"))
                locked = _to_float(item.get("frozenBal"))
                total = _to_float(item.get("eq")) or (free + locked)
                if total <= 0:
                    continue
                result.append({"asset": asset, "free": free, "locked": locked, "total": total})
        return result

    @staticmethod
    def _normalize_okx_positions(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in payload:
            qty = _to_float(item.get("pos"))
            if qty == 0:
                continue
            pos_side = str(item.get("posSide") or "").upper()
            if pos_side in {"LONG", "SHORT"}:
                side = pos_side
            else:
                side = "LONG" if qty > 0 else "SHORT"
            result.append(
                {
                    "symbol": str(item.get("instId", "")),
                    "side": side,
                    "quantity": abs(qty),
                    "entry_price": _to_float(item.get("avgPx")),
                    "mark_price": _to_float(item.get("markPx")),
                    "unrealized_pnl": _to_float(item.get("upl")),
                }
            )
        return result

    @staticmethod
    def _normalize_okx_orders(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in payload:
            result.append(
                {
                    "symbol": str(item.get("instId", "")),
                    "order_id": str(item.get("ordId", "")),
                    "client_order_id": item.get("clOrdId"),
                    "status": str(item.get("state", "")),
                    "side": str(item.get("side", "")),
                    "order_type": str(item.get("ordType", "")),
                    "price": _to_float(item.get("px")),
                    "quantity": _to_float(item.get("sz")),
                    "filled_quantity": _to_float(item.get("accFillSz")),
                    "avg_fill_price": _to_float_or_none(item.get("avgPx")),
                    "raw": item,
                }
            )
        return result

    @staticmethod
    def _normalize_okx_trades(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in payload:
            price = _to_float(item.get("fillPx"))
            quantity = _to_float(item.get("fillSz"))
            result.append(
                {
                    "symbol": str(item.get("instId", "")).upper(),
                    "order_id": str(item.get("ordId", "")),
                    "trade_id": str(item.get("tradeId", "")),
                    "side": str(item.get("side", "")).upper(),
                    "price": price,
                    "quantity": quantity,
                    "quote_quantity": price * quantity,
                    "fee": abs(_to_float(item.get("fee"))),
                    "fee_asset": item.get("feeCcy"),
                    "is_maker": str(item.get("execType", "")).upper() == "M",
                    "trade_time": item.get("ts"),
                    "raw": item,
                }
            )
        return result

    @staticmethod
    def _extract_lighter_account(payload: dict[str, Any], account_index: int) -> dict[str, Any] | None:
        accounts: list[dict[str, Any]] = []
        raw_accounts = payload.get("accounts")
        raw_sub_accounts = payload.get("sub_accounts")
        if isinstance(raw_accounts, list):
            accounts.extend(row for row in raw_accounts if isinstance(row, dict))
        if isinstance(raw_sub_accounts, list):
            accounts.extend(row for row in raw_sub_accounts if isinstance(row, dict))

        if not accounts:
            return None

        for row in accounts:
            idx = _to_int(row.get("account_index"), default=_to_int(row.get("index"), default=-1))
            if idx == account_index:
                return row
        # Fail closed when requested account_index is absent. Falling back to the
        # first returned account can silently bind the wrong Lighter sub-account.
        return None

    @staticmethod
    def _normalize_lighter_balances(account: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for asset in account.get("assets", []):
            if not isinstance(asset, dict):
                continue
            symbol = str(asset.get("symbol", "")).upper()
            if not symbol:
                continue
            total = _to_float(asset.get("balance"))
            locked = _to_float(asset.get("locked_balance"))
            free = max(total - locked, 0.0)
            if total <= 0 and free <= 0 and locked <= 0:
                continue
            result.append({"asset": symbol, "free": free, "locked": locked, "total": total})
        return result

    @staticmethod
    def _normalize_lighter_positions(
        account: dict[str, Any],
        market_symbols: dict[int, str],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in account.get("positions", []):
            if not isinstance(item, dict):
                continue
            market_id = _to_int(item.get("market_id"), default=-1)
            raw_size = _to_float(item.get("position"))
            if raw_size == 0:
                continue
            sign = _to_int(item.get("sign"), default=1)
            side = "LONG" if sign >= 0 else "SHORT"
            symbol = (
                str(item.get("symbol", "")).upper()
                or market_symbols.get(market_id, "")
                or f"MARKET-{market_id}"
            )

            result.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "quantity": abs(raw_size),
                    "entry_price": _to_float(item.get("avg_entry_price")),
                    "mark_price": _to_float_or_none(item.get("mark_price")),
                    "unrealized_pnl": _to_float_or_none(item.get("unrealized_pnl")),
                    "market_id": market_id,
                }
            )
        return result

    @staticmethod
    def _normalize_lighter_orders(payload: list[dict[str, Any]], market_symbols: dict[int, str]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            market_id = _to_int(item.get("market_index"), default=_to_int(item.get("market_id"), default=-1))
            symbol = market_symbols.get(market_id, f"MARKET-{market_id}")
            side = str(item.get("side", "")).upper()
            if not side:
                side = "SELL" if bool(item.get("is_ask")) else "BUY"
            filled = _to_float(item.get("filled_base_amount"))
            filled_quote = _to_float(item.get("filled_quote_amount"))
            avg_fill_price = (filled_quote / filled) if filled > 0 else None
            result.append(
                {
                    "symbol": symbol,
                    "order_id": str(item.get("order_id") or item.get("order_index") or ""),
                    "client_order_id": (
                        str(item.get("client_order_id"))
                        if item.get("client_order_id") not in (None, "")
                        else (
                            str(item.get("client_order_index"))
                            if item.get("client_order_index") not in (None, "")
                            else None
                        )
                    ),
                    "status": str(item.get("status", "")).upper(),
                    "side": side,
                    "order_type": str(item.get("type", "")).upper(),
                    "price": _to_float(item.get("price")),
                    "quantity": _to_float(item.get("initial_base_amount")),
                    "filled_quantity": filled,
                    "avg_fill_price": avg_fill_price,
                    "raw": item,
                }
            )
        return result

    @staticmethod
    def _normalize_lighter_trades(
        payload: list[dict[str, Any]],
        *,
        account_index: int,
        market_symbols: dict[int, str],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue

            market_id = _to_int(item.get("market_id"), default=-1)
            symbol = market_symbols.get(market_id, f"MARKET-{market_id}")
            ask_account_id = _to_int(item.get("ask_account_id"), default=-1)
            bid_account_id = _to_int(item.get("bid_account_id"), default=-1)
            is_maker_ask = bool(item.get("is_maker_ask"))

            if ask_account_id == account_index:
                side = "SELL"
                is_maker = is_maker_ask
                order_id = str(item.get("ask_id", ""))
            elif bid_account_id == account_index:
                side = "BUY"
                is_maker = not is_maker_ask
                order_id = str(item.get("bid_id", ""))
            else:
                # Keep a deterministic side even if server returns cross-account rows.
                side = "UNKNOWN"
                is_maker = False
                order_id = str(item.get("bid_id") or item.get("ask_id") or "")

            fee = _to_float(item.get("maker_fee")) if is_maker else _to_float(item.get("taker_fee"))
            price = _to_float(item.get("price"))
            quantity = _to_float(item.get("size"))
            quote_quantity = _to_float(item.get("usd_amount"), default=price * quantity)

            result.append(
                {
                    "symbol": symbol,
                    "order_id": order_id,
                    "trade_id": str(item.get("trade_id", "")),
                    "side": side,
                    "price": price,
                    "quantity": quantity,
                    "quote_quantity": quote_quantity,
                    "fee": abs(fee),
                    "fee_asset": None,
                    "is_maker": is_maker,
                    "trade_time": item.get("timestamp"),
                    "raw": item,
                }
            )
        return result

    @staticmethod
    def _build_binance_signature(api_secret: str, query_string: str) -> str:
        return hmac.new(api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _build_okx_signature(
        api_secret: str,
        timestamp: str,
        method: str,
        request_path: str,
        body: str,
    ) -> str:
        prehash = f"{timestamp}{method.upper()}{request_path}{body}"
        digest = hmac.new(api_secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")


def _extract_message(response: httpx.Response) -> str:
    try:
        payload: Any = response.json()
        if isinstance(payload, dict):
            return str(payload.get("msg") or payload.get("message") or payload)
        return str(payload)
    except Exception:
        return response.text[:300]


def _normalize_trade_cursors(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"symbols": {}, "global": {}}
    symbols = value.get("symbols")
    global_cursor = value.get("global")
    return {
        "symbols": symbols if isinstance(symbols, dict) else {},
        "global": global_cursor if isinstance(global_cursor, dict) else {},
    }


def _dedupe_lighter_trades(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = (
            str(row.get("trade_id") or ""),
            str(row.get("order_id") or ""),
            str(row.get("symbol") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _extract_trade_cursor_symbols(trade_cursors: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    normalized = _normalize_trade_cursors(trade_cursors)
    result: dict[str, dict[str, Any]] = {}
    for key, raw_value in normalized["symbols"].items():
        symbol = str(key or "").upper().strip()
        if not symbol:
            continue
        cursor = raw_value if isinstance(raw_value, dict) else {}
        result[symbol] = cursor
    return result


def _extract_global_trade_cursor_ms(trade_cursors: dict[str, Any] | None) -> int:
    normalized = _normalize_trade_cursors(trade_cursors)
    return _to_int(normalized["global"].get("last_trade_time_ms"), default=0)


def _lighter_trade_page_signature(raw_trades: list[dict[str, Any]]) -> str:
    if not raw_trades:
        return "empty"
    first = raw_trades[0] if isinstance(raw_trades[0], dict) else {}
    last = raw_trades[-1] if isinstance(raw_trades[-1], dict) else {}
    first_id = _pick_dict_value(first, "trade_id", "id")
    last_id = _pick_dict_value(last, "trade_id", "id")
    first_ts = _pick_dict_value(first, "timestamp", "ts", "time")
    last_ts = _pick_dict_value(last, "timestamp", "ts", "time")
    return f"{first_id}|{last_id}|{first_ts}|{last_ts}|{len(raw_trades)}"


def _build_lighter_next_trade_params(
    current_params: dict[str, Any],
    payload: dict[str, Any],
    raw_trades: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Build next-page params from either cursor token or fallback window hints.

    The API shape may differ by deployment. We first honor explicit cursor
    tokens when available. If only has_more-like flags exist, we push an older
    boundary (time/id) to avoid requesting the same page.
    """
    next_cursor = _pick_dict_value(payload, "next_cursor", "nextCursor", "cursor", "next")
    if next_cursor not in (None, ""):
        next_params = dict(current_params)
        for key in ("cursor", "next_cursor", "nextCursor"):
            next_params[key] = str(next_cursor)
        return next_params

    has_more = bool(
        _pick_dict_value(
            payload,
            "has_more",
            "hasMore",
            "has_next",
            "hasNext",
            "next_page",
            "nextPage",
        )
    )
    if not has_more:
        return None

    next_params = dict(current_params)
    oldest_time = _extract_oldest_trade_time_ms(raw_trades)
    oldest_id = _extract_oldest_trade_id(raw_trades)
    progressed = False

    if oldest_time > 0:
        boundary = max(oldest_time - 1, 0)
        if _to_int(next_params.get("timestamp_to"), default=-1) != boundary:
            progressed = True
        for key in ("timestamp_to", "end_time", "to_time"):
            next_params[key] = boundary
    if oldest_id not in (None, ""):
        normalized_id = str(oldest_id)
        if str(next_params.get("before_trade_id", "")) != normalized_id:
            progressed = True
        for key in ("before_trade_id", "before_id"):
            next_params[key] = normalized_id

    return next_params if progressed else None


def _extract_oldest_trade_time_ms(raw_trades: list[dict[str, Any]]) -> int:
    oldest = 0
    for row in raw_trades:
        if not isinstance(row, dict):
            continue
        value = _pick_dict_value(row, "timestamp", "ts", "time")
        timestamp = _to_int(value, default=0)
        if timestamp <= 0:
            continue
        # Some exchanges return seconds; normalize to ms for outgoing params.
        if timestamp < 10_000_000_000:
            timestamp = timestamp * 1000
        if oldest == 0 or timestamp < oldest:
            oldest = timestamp
    return oldest


def _extract_oldest_trade_id(raw_trades: list[dict[str, Any]]) -> str | None:
    oldest: int | None = None
    for row in raw_trades:
        if not isinstance(row, dict):
            continue
        raw_id = _pick_dict_value(row, "trade_id", "id")
        trade_id = _to_int(raw_id, default=-1)
        if trade_id < 0:
            continue
        if oldest is None or trade_id < oldest:
            oldest = trade_id
    if oldest is None:
        return None
    return str(oldest)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_json_dict(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _pick_dict_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _extract_lighter_exchange_payload(payload: dict[str, Any]) -> dict[str, Any]:
    # For POST /api/orders we carry Lighter signed tx fields inside `exchange_payload`.
    # For internal calls/tests we also allow direct dict shape.
    exchange_payload = payload.get("exchange_payload")
    if isinstance(exchange_payload, dict):
        return exchange_payload
    return payload if isinstance(payload, dict) else {}


def _to_float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return _to_float(value)


def _synthetic_error_response(message: str) -> httpx.Response:
    request = httpx.Request("GET", "https://synthetic.local/error")
    return httpx.Response(status_code=599, request=request, json={"message": message})


def _format_decimal(value: float) -> str:
    text = f"{value:.12f}".rstrip("0").rstrip(".")
    return text if text else "0"
