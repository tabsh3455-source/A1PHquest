from __future__ import annotations

from secrets import compare_digest

from fastapi import status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import get_settings

settings = get_settings()
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_CSRF_EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/auth/register",
}


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if not _requires_csrf(request):
            return await call_next(request)

        session_cookie = str(request.cookies.get(settings.auth_cookie_name) or "").strip()
        if not session_cookie:
            return await call_next(request)

        csrf_cookie = str(request.cookies.get(settings.csrf_cookie_name) or "").strip()
        csrf_header = str(request.headers.get(settings.csrf_header_name) or "").strip()
        if not csrf_cookie or not csrf_header or not compare_digest(csrf_cookie, csrf_header):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Invalid CSRF token"},
            )
        return await call_next(request)


def _requires_csrf(request: Request) -> bool:
    method = str(request.method or "").upper()
    if method in _SAFE_METHODS:
        return False
    path = str(request.url.path or "")
    if not path.startswith("/api/"):
        return False
    return path not in _CSRF_EXEMPT_PATHS
