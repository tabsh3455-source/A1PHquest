#!/usr/bin/env python3
"""End-to-end smoke test for auth + Google Authenticator login flow."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from random import randint
import struct
import time

import requests
import urllib3

BASE_URL = str(os.getenv("SMOKE_TEST_API_BASE") or "https://127.0.0.1").rstrip("/")
VERIFY_SSL = str(os.getenv("SMOKE_TEST_API_VERIFY_SSL") or "0").strip().lower() not in {"0", "false", "no"}
SESSION = requests.Session()

if BASE_URL.startswith("https://") and not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def generate_totp(secret: str, *, period: int = 30, digits: int = 6) -> str:
    padding = "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode((secret + padding).upper())
    counter = int(time.time() // period)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def request_json(
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    csrf_token: str | None = None,
    step_up_token: str | None = None,
) -> tuple[int, dict]:
    url = f"{BASE_URL}{path}"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
    if step_up_token:
        headers["X-StepUp-Token"] = step_up_token
    response = SESSION.request(
        method=method.upper(),
        url=url,
        json=payload,
        headers=headers,
        timeout=20,
        verify=VERIFY_SSL,
    )
    if not response.content:
        data = {}
    else:
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text[:500]}
    return response.status_code, data


def main() -> None:
    user_suffix = randint(10000, 99999)
    username = f"smoke_{user_suffix}"
    email = f"{username}@example.com"
    password = "StrongPass123!"

    unauth_readiness_status, unauth_readiness_data = request_json("GET", "/api/workflow/readiness")
    assert unauth_readiness_status == 200, (
        f"workflow readiness (unauth) failed: {unauth_readiness_status}, {unauth_readiness_data}"
    )
    assert (unauth_readiness_data.get("next_required_actions") or [{}])[0].get("code") == "sign_in"

    register_start_status, register_start_data = request_json(
        "POST",
        "/api/auth/register/start",
        {"username": username, "email": email, "password": password},
    )
    assert register_start_status == 201, f"register start failed: {register_start_status}, {register_start_data}"

    otp_secret = str(register_start_data["otp_secret"])
    registration_token = str(register_start_data["registration_token"])
    register_complete_status, register_complete_data = request_json(
        "POST",
        "/api/auth/register/complete",
        {
            "registration_token": registration_token,
            "otp_code": generate_totp(otp_secret),
        },
    )
    assert register_complete_status == 201, (
        f"register complete failed: {register_complete_status}, {register_complete_data}"
    )
    csrf_token = str(register_complete_data["csrf_token"])

    post_signup_readiness_status, post_signup_readiness_data = request_json(
        "GET",
        "/api/workflow/readiness",
        csrf_token=csrf_token,
    )
    assert post_signup_readiness_status == 200, (
        f"workflow readiness after signup failed: {post_signup_readiness_status}, {post_signup_readiness_data}"
    )
    assert (post_signup_readiness_data.get("next_required_actions") or [{}])[0].get("code") == "add_exchange_account"

    no_otp_status, no_otp_login_data = request_json(
        "POST",
        "/api/auth/login",
        {"username": username, "password": password},
        csrf_token=csrf_token,
    )
    assert no_otp_status == 400, f"login without otp should fail: {no_otp_status}, {no_otp_login_data}"

    final_login_status, final_login_data = request_json(
        "POST",
        "/api/auth/login",
        {"username": username, "password": password, "otp_code": generate_totp(otp_secret)},
        csrf_token=csrf_token,
    )
    assert final_login_status == 200, f"login with otp failed: {final_login_status}, {final_login_data}"
    csrf_token = str(final_login_data["csrf_token"])

    step_up_status, step_up_data = request_json(
        "POST",
        "/api/auth/2fa/step-up",
        {"code": generate_totp(otp_secret)},
        csrf_token=csrf_token,
    )
    assert step_up_status == 200, f"step-up failed: {step_up_status}, {step_up_data}"
    step_up_token = str(step_up_data["step_up_token"])

    risk_status, risk_data = request_json(
        "PUT",
        "/api/risk-rules",
        {
            "max_order_notional": 5000,
            "max_daily_loss": 1000,
            "max_position_ratio": 0.5,
            "max_cancel_rate_per_minute": 60,
            "circuit_breaker_enabled": True,
        },
        csrf_token=csrf_token,
        step_up_token=step_up_token,
    )
    assert risk_status == 200, f"risk rule upsert failed: {risk_status}, {risk_data}"

    account_status, account_data = request_json(
        "POST",
        "/api/exchange-accounts",
        {
            "exchange": "binance",
            "account_alias": "smoke-binance",
            "api_key": "smoke-key",
            "api_secret": "smoke-secret",
            "is_testnet": True,
        },
        csrf_token=csrf_token,
        step_up_token=step_up_token,
    )
    assert account_status == 201, f"exchange account create failed: {account_status}, {account_data}"
    exchange_account_id = int(account_data["id"])

    post_account_readiness_status, post_account_readiness_data = request_json(
        "GET",
        "/api/workflow/readiness",
        csrf_token=csrf_token,
    )
    assert post_account_readiness_status == 200, (
        f"workflow readiness after account failed: {post_account_readiness_status}, {post_account_readiness_data}"
    )
    post_account_action_codes = [item.get("code") for item in post_account_readiness_data.get("next_required_actions", [])]
    assert "create_strategy" in post_account_action_codes

    create_strategy_status, create_strategy_data = request_json(
        "POST",
        "/api/strategies",
        {
            "name": "smoke-futures-grid",
            "template_key": "futures_grid",
            "config": {
                "exchange_account_id": exchange_account_id,
                "symbol": "BTCUSDT",
                "grid_count": 8,
                "grid_step_pct": 0.5,
                "base_order_size": 0.001,
                "leverage": 3,
                "direction": "neutral",
            },
        },
        csrf_token=csrf_token,
    )
    assert create_strategy_status in {200, 201}, (
        f"strategy create failed: {create_strategy_status}, {create_strategy_data}"
    )
    strategy_id = int(create_strategy_data["id"])

    post_strategy_readiness_status, post_strategy_readiness_data = request_json(
        "GET",
        "/api/workflow/readiness",
        csrf_token=csrf_token,
    )
    assert post_strategy_readiness_status == 200, (
        f"workflow readiness after strategy failed: {post_strategy_readiness_status}, {post_strategy_readiness_data}"
    )
    post_strategy_action_codes = [item.get("code") for item in post_strategy_readiness_data.get("next_required_actions", [])]
    assert "start_strategy" in post_strategy_action_codes

    # Refresh step-up token before high-risk runtime start in case the first token is near expiry.
    step_up_refresh_status, step_up_refresh_data = request_json(
        "POST",
        "/api/auth/2fa/step-up",
        {"code": generate_totp(otp_secret)},
        csrf_token=csrf_token,
    )
    assert step_up_refresh_status == 200, (
        f"step-up refresh failed: {step_up_refresh_status}, {step_up_refresh_data}"
    )
    step_up_token = str(step_up_refresh_data["step_up_token"])

    start_strategy_status, start_strategy_data = request_json(
        "POST",
        f"/api/strategies/{strategy_id}/start",
        {},
        csrf_token=csrf_token,
        step_up_token=step_up_token,
    )
    assert start_strategy_status == 200, (
        f"futures grid start failed: {start_strategy_status}, {start_strategy_data}"
    )
    assert str(start_strategy_data.get("runtime_ref") or "").strip(), "runtime_ref should be non-empty"
    assert str(start_strategy_data.get("status") or "") in {"starting", "running", "failed"}

    final_readiness_status, final_readiness_data = request_json(
        "GET",
        "/api/workflow/readiness",
        csrf_token=csrf_token,
    )
    assert final_readiness_status == 200, (
        f"workflow readiness final failed: {final_readiness_status}, {final_readiness_data}"
    )
    assert bool(final_readiness_data.get("has_risk_rule")) is True
    assert int(final_readiness_data.get("running_live_strategy_instances_total") or 0) >= 1
    final_action_codes = [item.get("code") for item in final_readiness_data.get("next_required_actions", [])]
    assert "create_ai_provider" in final_action_codes
    assert "create_ai_policy" in final_action_codes

    result = {
        "workflow_readiness_unauth_status": unauth_readiness_status,
        "workflow_readiness_post_signup_status": post_signup_readiness_status,
        "workflow_readiness_post_account_status": post_account_readiness_status,
        "workflow_readiness_post_strategy_status": post_strategy_readiness_status,
        "workflow_readiness_final_status": final_readiness_status,
        "register_start_status": register_start_status,
        "register_complete_status": register_complete_status,
        "login_without_otp_status": no_otp_status,
        "login_with_otp_status": final_login_status,
        "risk_rule_status": risk_status,
        "exchange_account_status": account_status,
        "create_strategy_status": create_strategy_status,
        "start_strategy_status": start_strategy_status,
        "start_strategy_runtime_status": start_strategy_data.get("status"),
        "username": username,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
