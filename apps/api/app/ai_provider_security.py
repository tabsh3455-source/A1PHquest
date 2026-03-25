from __future__ import annotations

import ipaddress
from urllib.parse import urlparse, urlunparse

from fastapi import HTTPException

from .config import get_settings

settings = get_settings()


def normalize_and_validate_provider_base_url(base_url: str) -> str:
    parsed = urlparse(str(base_url or "").strip())
    if parsed.scheme not in {"https", "http"}:
        raise HTTPException(status_code=400, detail="AI provider base_url must use http or https")
    if not parsed.hostname or not parsed.netloc:
        raise HTTPException(status_code=400, detail="AI provider base_url must include a valid host")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="AI provider base_url must not include embedded credentials")
    if parsed.query or parsed.fragment:
        raise HTTPException(status_code=400, detail="AI provider base_url must not include query or fragment")

    hostname = str(parsed.hostname or "").strip().lower()
    host_allowlist = set(settings.ai_provider_allowed_host_list())
    is_allowlisted = hostname in host_allowlist
    is_private_host = is_private_or_local_host(hostname)
    if is_private_host and not (settings.ai_provider_allow_private_hosts or is_allowlisted):
        raise HTTPException(
            status_code=400,
            detail="AI provider base_url must not target localhost or private-network hosts",
        )
    if parsed.scheme != "https" and not (settings.ai_provider_allow_private_hosts or is_allowlisted):
        raise HTTPException(
            status_code=400,
            detail="AI provider base_url must use https unless the host is explicitly trusted",
        )

    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.rstrip("/"),
        path=parsed.path.rstrip("/"),
        params="",
        query="",
        fragment="",
    )
    return urlunparse(normalized).rstrip("/")


def is_private_or_local_host(hostname: str) -> bool:
    if hostname in {"localhost", "localhost.localdomain"}:
        return True
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return hostname.endswith(".local")
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )
