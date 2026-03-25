#!/usr/bin/env python3
"""Smoke test for exchange sync pipeline endpoint reachability and behavior."""

from __future__ import annotations

import json
from random import randint
import base64
import hashlib
import hmac
import struct
import time
import urllib.error
import urllib.request


BASE_URL = "http://127.0.0.1:8000"


def generate_totp(secret: str, *, period: int = 30, digits: int = 6) -> str:
    padding = "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode((secret + padding).upper())
    counter = int(time.time() // period)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def post_json(
    path: str,
    payload: dict,
    token: str | None = None,
    *,
    step_up_token: str | None = None,
) -> tuple[int, dict]:
    url = f"{BASE_URL}{path}"
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if step_up_token:
        headers["X-StepUp-Token"] = step_up_token
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
            return response.status, data
    except urllib.error.HTTPError as exc:
        data = {}
        if exc.fp:
            raw = exc.fp.read().decode("utf-8")
            if raw:
                data = json.loads(raw)
        return exc.code, data


def main() -> None:
    user_suffix = randint(10000, 99999)
    username = f"sync_{user_suffix}"
    email = f"{username}@example.com"
    password = "StrongPass123!"

    status, _ = post_json("/api/auth/register", {"username": username, "email": email, "password": password})
    assert status == 201, f"register failed: {status}"

    status, login_data = post_json("/api/auth/login", {"username": username, "password": password})
    assert status == 200, f"initial login failed: {status}"
    token = login_data["access_token"]

    status, setup_data = post_json("/api/auth/2fa/setup", {}, token=token)
    assert status == 200, f"2fa setup failed: {status}"
    otp_code = generate_totp(setup_data["otp_secret"])

    status, login_data = post_json(
        "/api/auth/login",
        {"username": username, "password": password, "otp_code": otp_code},
    )
    assert status == 200, f"2fa login failed: {status}"
    token = login_data["access_token"]

    status, step_up_data = post_json("/api/auth/2fa/step-up", {"code": otp_code}, token=token)
    assert status == 200, f"step-up failed: {status}, {step_up_data}"
    step_up_token = step_up_data["step_up_token"]

    status, account_data = post_json(
        "/api/exchange-accounts",
        {
            "exchange": "binance",
            "account_alias": "smoke-sync-binance",
            "api_key": "dummy_key",
            "api_secret": "dummy_secret",
            "is_testnet": True,
        },
        token=token,
        step_up_token=step_up_token,
    )
    assert status == 201, f"create exchange account failed: {status}, {account_data}"
    account_id = account_data["id"]

    # Invalid credentials should fail but endpoint should work and return structured error.
    status, sync_data = post_json(
        f"/api/exchange-accounts/{account_id}/sync",
        {},
        token=token,
        step_up_token=step_up_token,
    )
    assert status in (400, 502), f"sync endpoint unexpected status: {status}, {sync_data}"

    print(
        json.dumps(
            {
                "username": username,
                "account_id": account_id,
                "sync_status": status,
                "sync_error": sync_data.get("detail") if isinstance(sync_data, dict) else str(sync_data),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
