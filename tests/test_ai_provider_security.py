from fastapi import HTTPException

from app.ai_provider_security import normalize_and_validate_provider_base_url, settings


def test_provider_base_url_allows_public_https():
    assert normalize_and_validate_provider_base_url("https://api.openai.com/v1/") == "https://api.openai.com/v1"


def test_provider_base_url_blocks_localhost_by_default():
    try:
        normalize_and_validate_provider_base_url("http://localhost:11434/v1")
        raise AssertionError("Expected localhost AI endpoint to be blocked by default")
    except HTTPException as exc:
        assert exc.status_code == 400


def test_provider_base_url_blocks_private_ip_by_default():
    try:
        normalize_and_validate_provider_base_url("http://127.0.0.1:8001/v1")
        raise AssertionError("Expected private-network AI endpoint to be blocked by default")
    except HTTPException as exc:
        assert exc.status_code == 400


def test_provider_base_url_allows_explicit_trusted_private_host(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider_allow_private_hosts", False)
    monkeypatch.setattr(settings, "ai_provider_allowed_hosts", "localhost")

    assert normalize_and_validate_provider_base_url("http://localhost:11434/v1") == "http://localhost:11434/v1"
