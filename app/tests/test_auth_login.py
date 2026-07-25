import uuid

from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel


def test_login_generates_otp_for_existing_active_user(
    client, fake_redis, unique_phone, cleanup_identifiers, db_session
):
    cleanup_identifiers["phones"].append(unique_phone)
    user = UserModel(phone_number=unique_phone, status=UserStatus.ACTIVE, role=UserRole.CUSTOMER)
    db_session.add(user)
    db_session.commit()

    response = client.post("/app/auth/login", json={"phone_number": unique_phone})

    assert response.status_code == 200
    body = response.json()
    assert body["pending_id"] == str(user.id)

    otp_code = fake_redis.get(f"otp:register:{user.id}")
    assert otp_code is not None and len(otp_code) == 6


def test_login_then_verify_returns_token_with_existing_role(
    client, fake_redis, unique_phone, cleanup_identifiers, db_session
):
    cleanup_identifiers["phones"].append(unique_phone)
    user = UserModel(phone_number=unique_phone, status=UserStatus.ACTIVE, role=UserRole.ADMIN)
    db_session.add(user)
    db_session.commit()

    login_response = client.post("/app/auth/login", json={"phone_number": unique_phone})
    pending_id = login_response.json()["pending_id"]
    otp_code = fake_redis.get(f"otp:register:{pending_id}")

    verify_response = client.post(
        "/app/auth/verify", json={"pending_id": pending_id, "otp_code": otp_code}
    )

    assert verify_response.status_code == 200
    body = verify_response.json()
    assert body["user_id"] == str(user.id)
    assert body["role"] == "admin"


def test_login_with_unknown_identifier_gets_same_response_as_known(
    client, unique_phone, cleanup_identifiers, db_session
):
    # Anti-enumeration: an unknown number silently becomes a pending registration,
    # so the response never reveals whether an account exists.
    cleanup_identifiers["phones"].append(unique_phone)

    response = client.post("/app/auth/login", json={"phone_number": unique_phone})

    assert response.status_code == 200
    assert "pending_id" in response.json()

    user = db_session.query(UserModel).filter(UserModel.phone_number == unique_phone).first()
    assert user is not None
    assert user.status == UserStatus.PENDING


def test_login_works_for_pending_user_too(
    client, fake_redis, unique_phone, cleanup_identifiers, db_session
):
    cleanup_identifiers["phones"].append(unique_phone)
    user = UserModel(id=uuid.uuid4(), phone_number=unique_phone, status=UserStatus.PENDING)
    db_session.add(user)
    db_session.commit()

    response = client.post("/app/auth/login", json={"phone_number": unique_phone})

    assert response.status_code == 200
    assert response.json()["pending_id"] == str(user.id)


def test_login_without_identifier_returns_422(client):
    response = client.post("/app/auth/login", json={})
    assert response.status_code == 422
