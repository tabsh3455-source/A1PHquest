#!/usr/bin/env python3
"""End-to-end smoke test for auth + Google Authenticator login flow."""

from __future__ import annotations

import json
import os
from random import randint
import base64
import hashlib
import hmac
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


def post_json(path: str, payload: dict, csrf_token: str | None = None) -> tuple[int, dict]:
    url = f"{BASE_URL}{path}"
    headers = {"Content-Type": "application/json"}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
    response = SESSION.post(url, json=payload, headers=headers, timeout=15, verify=VERIFY_SSL)
    data = response.json() if response.content else {}
    return response.status_code, data


def main() -> None:
    user_suffix = randint(10000, 99999)
    username = f"smoke_{user_suffix}"
    email = f"{username}@example.com"
    password = "StrongPass123!"

    register_status, register_data = post_json(
        "/api/auth/register",
        {"username": username, "email": email, "password": password},
    )
    assert register_status == 201, f"register failed: {register_status}, {register_data}"

    login_status, login_data = post_json("/api/auth/login", {"username": username, "password": password})
    assert login_status == 200, f"initial login failed: {login_status}, {login_data}"
    csrf_token = login_data["csrf_token"]

    setup_status, setup_data = post_json("/api/auth/2fa/setup", {}, csrf_token=csrf_token)
    assert setup_status == 200, f"2fa setup failed: {setup_status}, {setup_data}"
    secret = setup_data["otp_secret"]

    no_otp_status, no_otp_login_data = post_json(
        "/api/auth/login",
        {"username": username, "password": password},
    )
    assert no_otp_status == 400, f"login without otp should fail: {no_otp_status}, {no_otp_login_data}"

    otp_code = generate_totp(secret)
    final_login_status, final_login_data = post_json(
        "/api/auth/login",
        {"username": username, "password": password, "otp_code": otp_code},
    )
    assert final_login_status == 200, f"login with otp failed: {final_login_status}, {final_login_data}"

    result = {
        "register_status": register_status,
        "login_without_otp_status": no_otp_status,
        "login_with_otp_status": final_login_status,
        "username": username,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
