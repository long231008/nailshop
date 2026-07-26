"""Changing an email requires a code sent to that address."""

from app.auth.infrastructure.models import UserModel


def test_email_is_not_editable_through_the_profile_endpoint(client, customer_identity, db_session):
    response = client.patch(
        "/app/me",
        json={"email": "claimed@example.com", "first_name": "Test"},
        headers=customer_identity["headers"],
    )

    assert response.status_code == 200
    user = db_session.get(UserModel, customer_identity["id"])
    db_session.refresh(user)
    assert user.email is None


def test_verified_email_change_flow(client, fake_redis, customer_identity, db_session):
    new_email = "verified-me@example.com"

    started = client.post(
        "/app/me/email", json={"email": new_email}, headers=customer_identity["headers"]
    )
    assert started.status_code == 200
    assert started.json()["expires_in_seconds"] > 0

    # Nothing has changed until the code comes back.
    user = db_session.get(UserModel, customer_identity["id"])
    db_session.refresh(user)
    assert user.email is None

    stored = fake_redis.hgetall(f"email_change:{customer_identity['id']}")
    assert stored["email"] == new_email
    code = stored["code"]

    wrong = client.post(
        "/app/me/email/confirm",
        json={"code": "000000" if code != "000000" else "111111"},
        headers=customer_identity["headers"],
    )
    assert wrong.status_code == 400

    confirmed = client.post(
        "/app/me/email/confirm", json={"code": code}, headers=customer_identity["headers"]
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["email"] == new_email

    db_session.refresh(user)
    assert user.email == new_email


def test_cannot_claim_an_email_another_account_already_uses(
    client, customer_identity, other_customer_headers, db_session, cleanup_identifiers
):
    taken = "taken@example.com"
    cleanup_identifiers["emails"].append(taken)

    # Give the other customer that address directly.
    others = (
        db_session.query(UserModel)
        .filter(UserModel.id != customer_identity["id"], UserModel.email.is_(None))
        .all()
    )
    victim = others[-1]
    victim.email = taken
    db_session.commit()

    response = client.post(
        "/app/me/email", json={"email": taken}, headers=customer_identity["headers"]
    )

    assert response.status_code == 409

    victim.email = None
    db_session.commit()


def test_confirm_without_a_pending_change_is_rejected(client, customer_identity):
    response = client.post(
        "/app/me/email/confirm", json={"code": "123456"}, headers=customer_identity["headers"]
    )
    assert response.status_code == 400
