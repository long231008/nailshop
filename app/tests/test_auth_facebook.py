import uuid
from urllib.parse import parse_qs, urlparse

import app.auth.presentation.router as router_module
from app.shared.infrastructure.config.settings import settings


def _configure_facebook(monkeypatch):
    monkeypatch.setattr(settings, "FACEBOOK_APP_ID", "test-app-id")
    monkeypatch.setattr(settings, "FACEBOOK_APP_SECRET", "test-app-secret")


def _mock_facebook(monkeypatch, email):
    monkeypatch.setattr(
        router_module, "exchange_code_for_access_token", lambda code: "fake-access-token"
    )
    monkeypatch.setattr(router_module, "fetch_user_email", lambda token: email)


def _login_and_get_state(client):
    response = client.get("/app/auth/facebook/login", follow_redirects=False)
    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert "facebook.com" in location
    return parse_qs(urlparse(location).query)["state"][0]


def test_facebook_login_unconfigured_returns_503(client):
    response = client.get("/app/auth/facebook/login", follow_redirects=False)
    assert response.status_code == 503


def test_facebook_login_redirects_and_stores_state(client, fake_redis, monkeypatch):
    _configure_facebook(monkeypatch)

    state = _login_and_get_state(client)

    assert fake_redis.get(f"oauth:facebook:state:{state}") == "1"


def test_facebook_callback_creates_new_active_customer(
    client, fake_redis, monkeypatch, cleanup_records
):
    _configure_facebook(monkeypatch)
    email = f"facebook-{uuid.uuid4().hex[:8]}@example.com"
    _mock_facebook(monkeypatch, email=email)
    state = _login_and_get_state(client)

    response = client.get(
        "/app/auth/facebook/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert location.startswith("http://localhost:5173/auth/callback#")
    params = dict(pair.split("=", 1) for pair in location.split("#", 1)[1].split("&"))
    assert params["role"] == "customer"
    assert params["token"]
    cleanup_records.append(("users", params["user_id"]))

    assert fake_redis.get(f"oauth:facebook:state:{state}") is None


def test_facebook_callback_rejects_invalid_state(client, monkeypatch):
    _configure_facebook(monkeypatch)
    response = client.get(
        "/app/auth/facebook/callback",
        params={"code": "fake-code", "state": "not-a-real-state"},
    )
    assert response.status_code == 400


def test_facebook_callback_rejects_account_without_email(client, fake_redis, monkeypatch):
    _configure_facebook(monkeypatch)
    _mock_facebook(monkeypatch, email=None)
    state = _login_and_get_state(client)

    response = client.get(
        "/app/auth/facebook/callback", params={"code": "fake-code", "state": state}
    )

    assert response.status_code == 400
