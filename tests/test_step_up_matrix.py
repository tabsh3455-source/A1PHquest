from fastapi.routing import APIRoute

from app.deps import require_step_up_user
from app.main import app


def test_high_risk_routes_require_step_up_token():
    expected = {
        ("POST", "/api/strategies/{strategy_id}/start"),
        ("POST", "/api/strategies/{strategy_id}/stop"),
        ("POST", "/api/orders"),
        ("POST", "/api/orders/{order_id}/cancel"),
        ("POST", "/api/exchange-accounts"),
        ("POST", "/api/exchange-accounts/{account_id}/validate"),
        ("POST", "/api/exchange-accounts/{account_id}/sync"),
        ("POST", "/api/exchange-accounts/{account_id}/lighter-reconcile/retry-sync"),
        ("PUT", "/api/risk-rules"),
    }

    found: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        deps = {dependency.call for dependency in route.dependant.dependencies}
        for method in route.methods:
            key = (method, route.path)
            if key not in expected:
                continue
            found.add(key)
            assert require_step_up_user in deps

    assert found == expected
