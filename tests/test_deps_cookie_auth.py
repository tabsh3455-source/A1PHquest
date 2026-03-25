from app.deps import get_access_token, get_access_token_optional


def test_get_access_token_optional_prefers_bearer_token():
    assert get_access_token_optional(bearer_token="bearer-token", cookie_token="cookie-token") == "bearer-token"


def test_get_access_token_optional_falls_back_to_cookie():
    assert get_access_token_optional(bearer_token=None, cookie_token="cookie-token") == "cookie-token"


def test_get_access_token_raises_when_missing():
    try:
        get_access_token(None)
        raise AssertionError("Expected authentication error when no token is present")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 401
