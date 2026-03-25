from types import SimpleNamespace

from app.routers import ws


def _fake_websocket(*, headers: dict[str, str] | None = None, query_token: str | None = None):
    query_params = {}
    if query_token is not None:
        query_params["token"] = query_token
    cookies = {}
    if headers and "cookie" in headers:
        for item in str(headers["cookie"]).split(";"):
            if "=" not in item:
                continue
            key, value = item.split("=", maxsplit=1)
            cookies[key.strip()] = value.strip()
    return SimpleNamespace(headers=headers or {}, query_params=query_params, cookies=cookies)


def test_extract_ws_token_prefers_authorization_header():
    websocket = _fake_websocket(headers={"authorization": "Bearer token-from-header"})
    assert ws._extract_ws_token(websocket) == "token-from-header"


def test_extract_ws_token_supports_bearer_protocol_pair():
    websocket = _fake_websocket(headers={"sec-websocket-protocol": "bearer, token-from-protocol"})
    assert ws._extract_ws_token(websocket) == "token-from-protocol"


def test_extract_ws_token_supports_auth_cookie():
    websocket = _fake_websocket(headers={"cookie": f"{ws.settings.auth_cookie_name}=token-from-cookie"})
    assert ws._extract_ws_token(websocket) == "token-from-cookie"


def test_extract_ws_token_disables_query_by_default():
    original = ws.settings.ws_allow_query_token
    ws.settings.ws_allow_query_token = False
    try:
        websocket = _fake_websocket(query_token="query-token")
        assert ws._extract_ws_token(websocket) is None
    finally:
        ws.settings.ws_allow_query_token = original


def test_extract_ws_token_allows_query_when_enabled():
    original = ws.settings.ws_allow_query_token
    ws.settings.ws_allow_query_token = True
    try:
        websocket = _fake_websocket(query_token="query-token")
        assert ws._extract_ws_token(websocket) == "query-token"
    finally:
        ws.settings.ws_allow_query_token = original
