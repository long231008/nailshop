import uuid
from urllib.parse import parse_qs, urlparse

import app.auth.presentation.router as router_module
from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel


def _mock_google(monkeypatch, email, email_verified=True):
    monkeypatch.setattr(
        router_module, "exchange_code_for_id_token", lambda code: "fake-id-token"
    )
    monkeypatch.setattr(
        router_module,
        "verify_id_token",
        lambda token: {
            "email": email,
            "email_verified": email_verified,
            "sub": "google-sub-123",
        },
    )


def _login_and_get_state(client):
    response = client.get("/app/auth/google/login", follow_redirects=False)
    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert "accounts.google.com" in location
    return parse_qs(urlparse(location).query)["state"][0]


def test_google_login_redirects_and_stores_state_in_redis(client, fake_redis):
    response = client.get("/app/auth/google/login", follow_redirects=False)

    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert "accounts.google.com" in location
    assert "client_id=" in location

    state = parse_qs(urlparse(location).query)["state"][0]
    assert fake_redis.get(f"oauth:google:state:{state}") == "1"


def test_google_callback_creates_new_active_customer(
    client, fake_redis, monkeypatch, cleanup_records
):
    email = f"google-{uuid.uuid4().hex[:8]}@example.com"
    _mock_google(monkeypatch, email=email)
    state = _login_and_get_state(client)

    response = client.get(
        "/app/auth/google/callback", params={"code": "fake-code", "state": state}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "customer"
    assert body["token_type"] == "bearer"
    cleanup_records.append(("users", body["user_id"]))

    assert fake_redis.get(f"oauth:google:state:{state}") is None


def test_google_callback_links_existing_user_by_email(
    client, fake_redis, monkeypatch, cleanup_records, db_session
):
    email = f"existing-{uuid.uuid4().hex[:8]}@example.com"
    existing_user = UserModel(email=email, status=UserStatus.PENDING, role=UserRole.CUSTOMER)
    db_session.add(existing_user)
    db_session.commit()
    cleanup_records.append(("users", existing_user.id))

    _mock_google(monkeypatch, email=email)
    state = _login_and_get_state(client)

    response = client.get(
        "/app/auth/google/callback", params={"code": "fake-code", "state": state}
    )

    assert response.status_code == 200
    assert response.json()["user_id"] == str(existing_user.id)

    db_session.refresh(existing_user)
    assert existing_user.status == UserStatus.ACTIVE


def test_google_callback_rejects_invalid_state(client):
    response = client.get(
        "/app/auth/google/callback",
        params={"code": "fake-code", "state": "not-a-real-state"},
    )
    assert response.status_code == 400


def test_google_callback_rejects_unverified_email(client, fake_redis, monkeypatch):
    _mock_google(monkeypatch, email="unverified@example.com", email_verified=False)
    state = _login_and_get_state(client)

    response = client.get(
        "/app/auth/google/callback", params={"code": "fake-code", "state": state}
    )

    assert response.status_code == 400
