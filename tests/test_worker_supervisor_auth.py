import httpx

from supervisor import main as supervisor_main


def test_worker_supervisor_runtime_routes_require_auth_token(monkeypatch, async_runner):
    monkeypatch.setenv("SUPERVISOR_SHARED_TOKEN", "x" * 32)

    async def _exercise() -> None:
        transport = httpx.ASGITransport(app=supervisor_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/runtime/non-existent")
            assert response.status_code == 403

            response = await client.get(
                "/runtime/non-existent",
                headers={"X-Supervisor-Token": "x" * 32},
            )
            assert response.status_code == 404

    async_runner(_exercise())


def test_worker_supervisor_runtime_routes_fail_when_token_not_configured(monkeypatch, async_runner):
    monkeypatch.delenv("SUPERVISOR_SHARED_TOKEN", raising=False)

    async def _exercise() -> None:
        transport = httpx.ASGITransport(app=supervisor_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                "/runtime/non-existent",
                headers={"X-Supervisor-Token": "x" * 32},
            )
            assert response.status_code == 503

    async_runner(_exercise())
