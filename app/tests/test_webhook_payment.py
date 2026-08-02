import hashlib
import hmac
import json
import uuid

from app.shared.infrastructure.config.settings import settings


def _sign(body: bytes) -> str:
    return hmac.new(settings.PAYMENT_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _create_booking(
    client,
    customer_headers,
    admin_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": seeded_shift["start"].isoformat(),
            }
        ],
    }
    booking = client.post("/app/bookings", json=payload, headers=customer_headers).json()
    cleanup_records.append(("bookings", booking["id"]))
    cleanup_records.append(("booking_details", booking["details"][0]["id"]))
    client.post(f"/app/bookings/{booking['id']}/approve", headers=admin_headers)
    return booking


def test_webhook_processes_valid_signature_and_clears_soft_lock(
    client,
    admin_headers,
    customer_headers,
    fake_redis,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
    db_session,
):
    booking = _create_booking(
        client,
        customer_headers,
        admin_headers,
        seeded_branch,
        seeded_service,
        seeded_staff,
        seeded_shift,
        cleanup_records,
    )
    assert fake_redis.get(f"booking:soft_lock:{booking['id']}") is not None

    transaction_id = str(uuid.uuid4())
    body = json.dumps(
        {
            "transaction_id": transaction_id,
            "booking_id": booking["id"],
            "amount": 6.0,
            "transaction_type": "deposit",
        }
    ).encode()
    signature = _sign(body)

    response = client.post(
        "/app/webhooks/payment",
        content=body,
        headers={"X-Signature": signature, "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["processed"] is True
    assert fake_redis.get(f"booking:soft_lock:{booking['id']}") is None

    from app.webhooks.infrastructure.models import PaymentTransactionModel

    transaction = (
        db_session.query(PaymentTransactionModel)
        .filter(PaymentTransactionModel.provider_transaction_id == transaction_id)
        .first()
    )
    cleanup_records.append(("payment_transactions", transaction.id))


def test_webhook_rejects_invalid_signature(client, seeded_branch):
    body = json.dumps(
        {
            "transaction_id": str(uuid.uuid4()),
            "booking_id": str(uuid.uuid4()),
            "amount": 6.0,
            "transaction_type": "deposit",
        }
    ).encode()

    response = client.post(
        "/app/webhooks/payment",
        content=body,
        headers={"X-Signature": "not-a-real-signature", "Content-Type": "application/json"},
    )

    assert response.status_code == 401


def test_webhook_is_idempotent_on_replay(
    client,
    admin_headers,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
    db_session,
):
    booking = _create_booking(
        client,
        customer_headers,
        admin_headers,
        seeded_branch,
        seeded_service,
        seeded_staff,
        seeded_shift,
        cleanup_records,
    )
    transaction_id = str(uuid.uuid4())
    body = json.dumps(
        {
            "transaction_id": transaction_id,
            "booking_id": booking["id"],
            "amount": 6.0,
            "transaction_type": "deposit",
        }
    ).encode()
    signature = _sign(body)
    headers = {"X-Signature": signature, "Content-Type": "application/json"}

    first = client.post("/app/webhooks/payment", content=body, headers=headers)
    second = client.post("/app/webhooks/payment", content=body, headers=headers)

    assert first.status_code == 200
    assert first.json()["processed"] is True
    assert second.status_code == 200
    # A replay reports the stored outcome: still processed, so a provider that
    # retries until it sees true stops retrying.
    assert second.json()["processed"] is True

    from app.webhooks.infrastructure.models import PaymentTransactionModel

    transaction = (
        db_session.query(PaymentTransactionModel)
        .filter(PaymentTransactionModel.provider_transaction_id == transaction_id)
        .first()
    )
    cleanup_records.append(("payment_transactions", transaction.id))


def test_webhook_rejects_deposit_before_approval(
    client,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
    db_session,
):
    # No approve step: the booking has no deposit_amount, so nothing is due yet.
    payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": seeded_shift["start"].isoformat(),
            }
        ],
    }
    booking = client.post("/app/bookings", json=payload, headers=customer_headers).json()
    cleanup_records.append(("bookings", booking["id"]))
    cleanup_records.append(("booking_details", booking["details"][0]["id"]))

    transaction_id = str(uuid.uuid4())
    body = json.dumps(
        {
            "transaction_id": transaction_id,
            "booking_id": booking["id"],
            "amount": 6.0,
            "transaction_type": "deposit",
        }
    ).encode()

    response = client.post(
        "/app/webhooks/payment",
        content=body,
        headers={"X-Signature": _sign(body), "Content-Type": "application/json"},
    )

    assert response.status_code == 400

    from app.bookings.infrastructure.models import BookingModel, BookingStatus
    from app.webhooks.infrastructure.models import (
        PaymentTransactionModel,
        PaymentTransactionStatus,
    )

    transaction = (
        db_session.query(PaymentTransactionModel)
        .filter(PaymentTransactionModel.provider_transaction_id == transaction_id)
        .first()
    )
    cleanup_records.append(("payment_transactions", transaction.id))
    # Recorded as FAILED, so the expiry sweep does not treat it as a paid deposit.
    assert transaction.status == PaymentTransactionStatus.FAILED
    refreshed = db_session.get(BookingModel, uuid.UUID(booking["id"]))
    assert refreshed.status == BookingStatus.PENDING
