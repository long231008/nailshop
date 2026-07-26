from fastapi import status
from app.shared.infrastructure.rate_limit import client_ip


def test_security_headers_middleware(client):
    resp = client.get("/docs")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "geolocation=()" in resp.headers.get("Permissions-Policy", "")


def test_request_logging_middleware_injects_request_id(client):
    resp = client.get("/docs")
    assert resp.status_code == status.HTTP_200_OK
    assert "X-Request-ID" in resp.headers
    assert len(resp.headers["X-Request-ID"]) > 0


class DummyClient:
    def __init__(self, host: str):
        self.host = host


class DummyRequest:
    def __init__(self, headers: dict | None = None, client_host: str = "127.0.0.1"):
        self.headers = headers or {}
        self.client = DummyClient(client_host)


def test_client_ip_extraction_without_trust_proxy(monkeypatch):
    from app.shared.infrastructure.config import settings
    monkeypatch.setattr(settings.settings, "TRUST_PROXY_HEADERS", False)
    
    req = DummyRequest(headers={"X-Forwarded-For": "203.0.113.195, 70.41.3.18"}, client_host="10.0.0.1")
    ip = client_ip(req)
    # When TRUST_PROXY_HEADERS is False, X-Forwarded-For is ignored
    assert ip == "10.0.0.1"


def test_client_ip_extraction_with_trust_proxy(monkeypatch):
    from app.shared.infrastructure.config import settings
    monkeypatch.setattr(settings.settings, "TRUST_PROXY_HEADERS", True)
    
    req = DummyRequest(headers={"X-Forwarded-For": "203.0.113.195, 70.41.3.18"}, client_host="10.0.0.1")
    ip = client_ip(req)
    # When TRUST_PROXY_HEADERS is True, the client IP is extracted from X-Forwarded-For
    assert ip == "203.0.113.195"
