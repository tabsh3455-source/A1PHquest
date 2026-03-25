from __future__ import annotations

from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .config import get_settings

settings = get_settings()
_ALLOW_METHODS = "DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT"


class SameHostCORSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        origin = str(request.headers.get("origin") or "").strip()
        if not origin:
            return await call_next(request)

        if not _is_allowed_origin(origin=origin, request=request):
            return await call_next(request)

        if request.method.upper() == "OPTIONS" and request.headers.get("access-control-request-method"):
            response = Response(status_code=204)
        else:
            response = await call_next(request)

        _apply_cors_headers(
            response=response,
            origin=origin,
            requested_headers=str(request.headers.get("access-control-request-headers") or "").strip(),
        )
        return response


def _is_allowed_origin(*, origin: str, request: Request) -> bool:
    normalized_origin = origin.rstrip("/")
    allowed_origins = {item.rstrip("/") for item in settings.cors_allowed_origin_list()}
    if normalized_origin in allowed_origins:
        return True

    origin_host = _hostname_from_origin(origin)
    request_host = _hostname_from_host_header(str(request.headers.get("host") or ""))
    if not origin_host or not request_host:
        return False
    return origin_host == request_host


def _apply_cors_headers(*, response: Response, origin: str, requested_headers: str) -> None:
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = _ALLOW_METHODS
    response.headers["Access-Control-Allow-Headers"] = requested_headers or "*"
    response.headers["Vary"] = _append_vary_header(response.headers.get("Vary"), "Origin")


def _append_vary_header(current: str | None, value: str) -> str:
    existing = [item.strip() for item in str(current or "").split(",") if item.strip()]
    if value not in existing:
        existing.append(value)
    return ", ".join(existing)


def _hostname_from_origin(origin: str) -> str:
    try:
        parsed = urlparse(origin)
    except ValueError:
        return ""
    return str(parsed.hostname or "").strip().lower()


def _hostname_from_host_header(host_header: str) -> str:
    value = str(host_header or "").strip().lower()
    if not value:
        return ""
    if value.startswith("[") and "]" in value:
        return value[1 : value.index("]")]
    if ":" in value:
        return value.rsplit(":", 1)[0]
    return value
