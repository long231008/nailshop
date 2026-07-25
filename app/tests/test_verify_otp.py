import uuid

from jose import jwt

from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.shared.infrastructure.config.settings import settings


def _register(client, fake_redis, phone_number):
    response = client.post("/app/auth/register", json={"phone_number": phone_number})
    pending_id = response.json()["pending_id"]
    otp_code = fake_redis.get(f"otp:register:{pending_id}")
    return pending_id, otp_code


def test_verify_otp_with_valid_code_activates_user_and_returns_jwt(
    client, fake_redis, unique_phone, cleanup_identifiers, db_session
):
    cleanup_identifiers["phones"].append(unique_phone)
    pending_id, otp_code = _register(client, fake_redis, unique_phone)

    response = client.post(
        "/app/auth/verify", json={"pending_id": pending_id, "otp_code": otp_code}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user_id"] == pending_id
    assert body["role"] == "customer"

    payload = jwt.decode(
        body["access_token"], settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
    )
    assert payload["user_id"] == pending_id
    assert payload["role"] == "customer"

    user = db_session.get(UserModel, uuid.UUID(pending_id))
    assert user.status == UserStatus.ACTIVE
    assert user.role == UserRole.CUSTOMER

    assert fake_redis.get(f"otp:register:{pending_id}") is None


def test_verify_otp_cannot_be_reused(client, fake_redis, unique_phone, cleanup_identifiers):
    cleanup_identifiers["phones"].append(unique_phone)
    pending_id, otp_code = _register(client, fake_redis, unique_phone)

    first = client.post("/app/auth/verify", json={"pending_id": pending_id, "otp_code": otp_code})
    assert first.status_code == 200

    second = client.post("/app/auth/verify", json={"pending_id": pending_id, "otp_code": otp_code})
    assert second.status_code == 400


def test_verify_otp_with_wrong_code_returns_400(
    client, fake_redis, unique_phone, cleanup_identifiers
):
    cleanup_identifiers["phones"].append(unique_phone)
    pending_id, otp_code = _register(client, fake_redis, unique_phone)
    wrong_code = "000000" if otp_code != "000000" else "111111"

    response = client.post(
        "/app/auth/verify", json={"pending_id": pending_id, "otp_code": wrong_code}
    )

    assert response.status_code == 400


def test_verify_otp_with_unknown_pending_id_returns_404(client):
    response = client.post(
        "/app/auth/verify",
        json={"pending_id": str(uuid.uuid4()), "otp_code": "123456"},
    )

    assert response.status_code == 404


def test_verify_otp_with_malformed_code_returns_422(client):
    response = client.post(
        "/app/auth/verify",
        json={"pending_id": str(uuid.uuid4()), "otp_code": "abc"},
    )

    assert response.status_code == 422
