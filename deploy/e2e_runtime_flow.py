#!/usr/bin/env python3
"""One-click E2E runtime flow: auth -> account -> strategy -> start/runtime/stop."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import random
import struct
import time
from urllib import error, request


def _post_json(
    base_url: str,
    path: str,
    payload: dict,
    token: str | None = None,
    *,
    step_up_token: str | None = None,
) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if step_up_token:
        headers["X-StepUp-Token"] = step_up_token
    req = request.Request(f"{base_url}{path}", data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        if not raw:
            return exc.code, {}
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}
    except Exception as exc:  # pragma: no cover - network/restart window
        return 599, {"error": str(exc)}


def _get_json(
    base_url: str,
    path: str,
    token: str | None = None,
    *,
    step_up_token: str | None = None,
) -> tuple[int, dict]:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if step_up_token:
        headers["X-StepUp-Token"] = step_up_token
    req = request.Request(f"{base_url}{path}", headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        if not raw:
            return exc.code, {}
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}
    except Exception as exc:  # pragma: no cover - network/restart window
        return 599, {"error": str(exc)}


def _with_retry(
    request_fn,
    *,
    attempts: int,
    retry_statuses: set[int] | None = None,
    delay_seconds: float = 1.0,
) -> tuple[int, dict]:
    retry_codes = retry_statuses or {500, 502, 503, 504, 599}
    last_status = 599
    last_data: dict = {}
    for _ in range(max(attempts, 1)):
        status, data = request_fn()
        last_status, last_data = status, data
        if status not in retry_codes:
            return status, data
        time.sleep(delay_seconds)
    return last_status, last_data


def _generate_totp(secret: str, *, period: int = 30, digits: int = 6) -> str:
    """Generate RFC6238 TOTP code without external pyotp dependency."""
    padding = "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode((secret + padding).upper())
    counter = int(time.time() // period)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    otp = binary % (10**digits)
    return str(otp).zfill(digits)


def _wait_consistency(base_url: str, token: str, strategy_id: int, timeout_seconds: int = 30) -> dict:
    deadline = time.time() + timeout_seconds
    latest: dict = {}
    while time.time() < deadline:
        # Refresh runtime snapshot first so DB observability fields can catch up
        # with supervisor heartbeat updates before strict consistency comparison.
        _get_json(base_url, f"/api/strategies/{strategy_id}/runtime", token=token)
        status, body = _get_json(base_url, f"/api/strategies/{strategy_id}/runtime/consistency", token=token)
        if status == 200:
            latest = body
            if body.get("consistent"):
                return body
        time.sleep(1.0)
    return latest


def main() -> int:
    parser = argparse.ArgumentParser(description="Run E2E strategy runtime flow.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--exchange", default="binance", choices=["binance", "okx"])
    parser.add_argument("--api-key", default="dummy_key")
    parser.add_argument("--api-secret", default="dummy_secret")
    parser.add_argument("--passphrase", default="")
    parser.add_argument("--timeout-seconds", type=int, default=30)
    args = parser.parse_args()

    suffix = random.randint(10000, 99999)
    username = f"e2e_{suffix}"
    email = f"{username}@example.com"
    password = "StrongPass123!"
    symbol = "BTCUSDT" if args.exchange == "binance" else "BTC-USDT-SWAP"
    passphrase = args.passphrase
    if args.exchange == "okx" and not passphrase:
        # OKX account creation enforces passphrase; use a deterministic placeholder
        # in smoke tests when caller does not provide one.
        passphrase = "okx_e2e_passphrase"

    # 1) Register user
    register_status, register_data = _with_retry(
        lambda: _post_json(
            args.base_url,
            "/api/auth/register",
            {"username": username, "email": email, "password": password},
        ),
        attempts=args.timeout_seconds,
    )
    assert register_status == 201, f"register failed: {register_status}, {register_data}"

    # 2) Login and setup 2FA (required for high-risk endpoints)
    login_status, login_data = _with_retry(
        lambda: _post_json(
            args.base_url,
            "/api/auth/login",
            {"username": username, "password": password},
        ),
        attempts=args.timeout_seconds,
    )
    assert login_status == 200, f"login failed: {login_status}, {login_data}"
    bootstrap_token = login_data["access_token"]

    setup_status, setup_data = _with_retry(
        lambda: _post_json(args.base_url, "/api/auth/2fa/setup", {}, token=bootstrap_token),
        attempts=args.timeout_seconds,
    )
    assert setup_status == 200, f"2fa setup failed: {setup_status}, {setup_data}"
    otp_code = _generate_totp(setup_data["otp_secret"])

    # 3) Login again with OTP to get high-risk operation token
    login_2fa_status, login_2fa_data = _with_retry(
        lambda: _post_json(
            args.base_url,
            "/api/auth/login",
            {"username": username, "password": password, "otp_code": otp_code},
        ),
        attempts=args.timeout_seconds,
    )
    assert login_2fa_status == 200, f"2fa login failed: {login_2fa_status}, {login_2fa_data}"
    token = login_2fa_data["access_token"]

    # 3.5) Perform step-up OTP verification for high-risk operations.
    step_up_status, step_up_data = _with_retry(
        lambda: _post_json(
            args.base_url,
            "/api/auth/2fa/step-up",
            {"code": _generate_totp(setup_data["otp_secret"])},
            token=token,
        ),
        attempts=args.timeout_seconds,
    )
    assert step_up_status == 200, f"step-up failed: {step_up_status}, {step_up_data}"
    step_up_token = step_up_data["step_up_token"]

    # 4) Create exchange account
    create_account_status, create_account_data = _with_retry(
        lambda: _post_json(
            args.base_url,
            "/api/exchange-accounts",
            {
                "exchange": args.exchange,
                "account_alias": f"e2e-{args.exchange}",
                "api_key": args.api_key,
                "api_secret": args.api_secret,
                "passphrase": passphrase or None,
                "is_testnet": True,
            },
            token=token,
            step_up_token=step_up_token,
        ),
        attempts=args.timeout_seconds,
    )
    assert create_account_status == 201, f"create account failed: {create_account_status}, {create_account_data}"
    account_id = create_account_data["id"]

    # 5) Create grid strategy
    create_strategy_status, create_strategy_data = _with_retry(
        lambda: _post_json(
            args.base_url,
            "/api/strategies",
            {
                "name": "e2e-grid",
                "strategy_type": "grid",
                "config": {
                    "exchange_account_id": account_id,
                    "symbol": symbol,
                    "grid_count": 8,
                    "grid_step_pct": 0.5,
                    "base_order_size": 0.001,
                },
            },
            token=token,
        ),
        attempts=args.timeout_seconds,
    )
    assert (
        create_strategy_status == 201
    ), f"create strategy failed: {create_strategy_status}, {create_strategy_data}"
    strategy_id = create_strategy_data["id"]

    # 6) Start strategy runtime (retry during transient service restart windows)
    start_status, start_data = _with_retry(
        lambda: _post_json(
            args.base_url,
            f"/api/strategies/{strategy_id}/start",
            {},
            token=token,
            step_up_token=step_up_token,
        ),
        attempts=args.timeout_seconds,
    )
    if start_status == 409 and "already running" in str(start_data.get("detail", "")).lower():
        # Start request is idempotent for test flow: if runtime already moved to running,
        # we continue by querying current runtime state.
        start_status = 200
        _, start_data = _get_json(
            args.base_url,
            f"/api/strategies/{strategy_id}/runtime",
            token=token,
        )
    assert start_status == 200, f"start strategy failed: {start_status}, {start_data}"

    # 7) Query runtime status
    runtime_status, runtime_data = _with_retry(
        lambda: _get_json(
            args.base_url,
            f"/api/strategies/{strategy_id}/runtime",
            token=token,
        ),
        attempts=args.timeout_seconds,
    )
    assert runtime_status == 200, f"runtime query failed: {runtime_status}, {runtime_data}"

    # 8) Runtime observability consistency check (DB row vs supervisor runtime state)
    consistency = _wait_consistency(args.base_url, token, strategy_id, timeout_seconds=args.timeout_seconds)
    assert consistency.get("consistent") is True, f"runtime consistency failed: {consistency}"

    # 9) Stop strategy when runtime is stoppable
    final_status = runtime_data.get("status", "")
    stop_status = None
    stop_data: dict = {}
    if final_status in {"running", "starting", "stopping"}:
        stop_status, stop_data = _with_retry(
            lambda: _post_json(
                args.base_url,
                f"/api/strategies/{strategy_id}/stop",
                {},
                token=token,
                step_up_token=step_up_token,
            ),
            attempts=args.timeout_seconds,
        )
        assert stop_status == 200, f"stop strategy failed: {stop_status}, {stop_data}"

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "username": username,
        "exchange": args.exchange,
        "strategy_id": strategy_id,
        "start_status": start_status,
        "runtime_status": runtime_data.get("status"),
        "runtime_last_error": runtime_data.get("last_error"),
        "consistency": consistency,
        "stop_status": stop_status,
        "stop_runtime_status": stop_data.get("status") if stop_data else None,
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
