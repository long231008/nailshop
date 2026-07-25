import uuid

from app.auth.domain.value_object import UserStatus
from app.auth.infrastructure.models import UserModel


def test_register_with_phone_creates_pending_user_and_otp(
    client, fake_redis, unique_phone, cleanup_identifiers, db_session
):
    cleanup_identifiers["phones"].append(unique_phone)

    response = client.post("/app/auth/register", json={"phone_number": unique_phone})

    assert response.status_code == 201
    body = response.json()
    assert body["expires_in_seconds"] == 300
    pending_id = uuid.UUID(body["pending_id"])

    otp_key = f"otp:register:{pending_id}"
    otp_code = fake_redis.get(otp_key)
    assert otp_code is not None
    assert len(otp_code) == 6
    assert otp_code.isdigit()
    assert 0 < fake_redis.ttl(otp_key) <= 300

    user = db_session.get(UserModel, pending_id)
    assert user is not None
    assert user.phone_number == unique_phone
    assert user.status == UserStatus.PENDING
    assert user.password_hash is None


def test_register_with_email_creates_pending_user(
    client, fake_redis, unique_email, cleanup_identifiers, db_session
):
    cleanup_identifiers["emails"].append(unique_email)

    response = client.post("/app/auth/register", json={"email": unique_email})

    assert response.status_code == 201
    pending_id = uuid.UUID(response.json()["pending_id"])

    user = db_session.get(UserModel, pending_id)
    assert user.email == unique_email
    assert user.status == UserStatus.PENDING


def test_register_again_while_pending_reuses_row_and_regenerates_otp(
    client, fake_redis, unique_phone, cleanup_identifiers, db_session
):
    cleanup_identifiers["phones"].append(unique_phone)

    first = client.post("/app/auth/register", json={"phone_number": unique_phone})
    first_pending_id = first.json()["pending_id"]
    first_otp = fake_redis.get(f"otp:register:{first_pending_id}")

    second = client.post("/app/auth/register", json={"phone_number": unique_phone})
    second_pending_id = second.json()["pending_id"]
    second_otp = fake_redis.get(f"otp:register:{second_pending_id}")

    assert second.status_code == 201
    assert second_pending_id == first_pending_id
    assert second_otp != first_otp

    count = db_session.query(UserModel).filter(UserModel.phone_number == unique_phone).count()
    assert count == 1


def test_register_without_identifier_returns_422(client):
    response = client.post("/app/auth/register", json={})

    assert response.status_code == 422


def test_register_conflict_when_identifier_already_active(
    client, unique_phone, cleanup_identifiers, db_session
):
    cleanup_identifiers["phones"].append(unique_phone)

    db_session.add(
        UserModel(
            id=uuid.uuid4(),
            phone_number=unique_phone,
            status=UserStatus.ACTIVE,
        )
    )
    db_session.commit()

    response = client.post("/app/auth/register", json={"phone_number": unique_phone})

    assert response.status_code == 409
