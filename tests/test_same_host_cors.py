from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.same_host_cors import SameHostCORSMiddleware


def _build_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(SameHostCORSMiddleware)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return TestClient(app)


def test_same_host_cors_allows_same_hostname_with_different_port():
    client = _build_client()
    response = client.get(
        "/ping",
        headers={
            "Host": "43.133.58.216:8000",
            "Origin": "http://43.133.58.216:5173",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://43.133.58.216:5173"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_same_host_cors_does_not_reflect_unrelated_origin():
    client = _build_client()
    response = client.get(
        "/ping",
        headers={
            "Host": "43.133.58.216:8000",
            "Origin": "http://evil.example:5173",
        },
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
