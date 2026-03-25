from app.security import (
    build_totp_uri,
    create_access_token,
    create_step_up_token,
    decode_access_token,
    generate_totp_secret,
    hash_password,
    verify_password,
    verify_totp,
)
import pyotp


def test_password_hash_and_verify():
    password = "StrongPass123!"
    password_hash = hash_password(password)
    assert password_hash != password
    assert verify_password(password, password_hash)


def test_access_token_encode_decode_roundtrip():
    token = create_access_token("7", {"role": "user", "token_version": 2})
    payload = decode_access_token(token)
    assert payload["sub"] == "7"
    assert payload["role"] == "user"
    assert payload["token_use"] == "access"
    assert payload["token_version"] == 2
    assert "iat" in payload


def test_access_token_decoder_rejects_step_up_token():
    token = create_step_up_token("7")
    try:
        decode_access_token(token)
        assert False, "decode_access_token must reject step-up tokens"
    except ValueError:
        pass


def test_totp_generate_uri_and_verify():
    secret = generate_totp_secret()
    uri = build_totp_uri(secret, "alice")
    assert "otpauth://" in uri

    code = pyotp.TOTP(secret).now()
    assert verify_totp(secret, code)
