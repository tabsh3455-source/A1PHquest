from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.csrf import CSRFMiddleware

settings = get_settings()


def _build_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(CSRFMiddleware)

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.post("/api/protected")
    def protected():
        return {"ok": True}

    @app.post("/api/auth/login")
    def login():
        return {"ok": True}

    return TestClient(app)


def test_csrf_middleware_allows_safe_methods_without_token():
    client = _build_client()
    response = client.get("/api/ping")
    assert response.status_code == 200


def test_csrf_middleware_blocks_cookie_authenticated_unsafe_request_without_header():
    client = _build_client()
    client.cookies.set(settings.auth_cookie_name, "session-cookie")
    client.cookies.set(settings.csrf_cookie_name, "csrf-cookie")

    response = client.post("/api/protected")
    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid CSRF token"


def test_csrf_middleware_allows_matching_csrf_header():
    client = _build_client()
    client.cookies.set(settings.auth_cookie_name, "session-cookie")
    client.cookies.set(settings.csrf_cookie_name, "csrf-cookie")

    response = client.post(
        "/api/protected",
        headers={settings.csrf_header_name: "csrf-cookie"},
    )
    assert response.status_code == 200


def test_csrf_middleware_exempts_login_endpoint():
    client = _build_client()
    client.cookies.set(settings.auth_cookie_name, "session-cookie")

    response = client.post("/api/auth/login")
    assert response.status_code == 200
