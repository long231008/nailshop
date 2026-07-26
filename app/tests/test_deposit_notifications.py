"""Deposit outcome notifications go out on the customer's own channel."""

import hashlib
import hmac
import json
import uuid

from app.main import app
from app.notification.infrastructure.senders import get_notification_sender
from app.shared.infrastructure.config.settings import settings


class RecordingSender:
    def __init__(self):
        self.sent = []

    def send(self, notification):
        self.sent.append(notification)


def _sign(body: bytes) -> str:
    return hmac.new(settings.PAYMENT_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _approved_booking(
    client,
    customer_headers,
    admin_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    booking = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "staff_id": str(seeded_staff["staff_id"]),
                    "start_time": seeded_shift["start"].isoformat(),
                }
            ],
        },
        headers=customer_headers,
    ).json()
    cleanup_records.append(("bookings", booking["id"]))
    cleanup_records.append(("booking_details", booking["details"][0]["id"]))
    client.post(f"/app/bookings/{booking['id']}/approve", headers=admin_headers)
    return booking


def _webhook(client, booking_id, amount, status=None):
    payload = {
        "transaction_id": str(uuid.uuid4()),
        "booking_id": booking_id,
        "amount": amount,
        "transaction_type": "deposit",
    }
    if status is not None:
        payload["status"] = status
    body = json.dumps(payload).encode()
    return client.post(
        "/app/webhooks/payment",
        content=body,
        headers={"X-Signature": _sign(body), "Content-Type": "application/json"},
    )


def test_successful_deposit_sends_confirmation(
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
    recorder = RecordingSender()
    app.dependency_overrides[get_notification_sender] = lambda: recorder

    booking = _approved_booking(
        client,
        customer_headers,
        admin_headers,
        seeded_branch,
        seeded_service,
        seeded_staff,
        seeded_shift,
        cleanup_records,
    )
    response = _webhook(client, booking["id"], 6.0)
    assert response.status_code == 200

    from app.webhooks.infrastructure.models import PaymentTransactionModel

    transaction = (
        db_session.query(PaymentTransactionModel)
        .filter(PaymentTransactionModel.booking_id == booking["id"])
        .first()
    )
    cleanup_records.append(("payment_transactions", transaction.id))

    subjects = [n.subject for n in recorder.sent]
    assert "Your booking is confirmed" in subjects
    confirmed = next(n for n in recorder.sent if n.subject == "Your booking is confirmed")
    # Delivered to the identifier the customer signed up with (phone here).
    assert confirmed.phone_number is not None


def test_failed_deposit_sends_failure_notice(
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
    recorder = RecordingSender()
    app.dependency_overrides[get_notification_sender] = lambda: recorder

    booking = _approved_booking(
        client,
        customer_headers,
        admin_headers,
        seeded_branch,
        seeded_service,
        seeded_staff,
        seeded_shift,
        cleanup_records,
    )
    response = _webhook(client, booking["id"], 6.0, status="failed")
    assert response.status_code == 200
    assert response.json()["processed"] is False

    from app.webhooks.infrastructure.models import PaymentTransactionModel

    transaction = (
        db_session.query(PaymentTransactionModel)
        .filter(PaymentTransactionModel.booking_id == booking["id"])
        .first()
    )
    cleanup_records.append(("payment_transactions", transaction.id))

    subjects = [n.subject for n in recorder.sent]
    assert "Your deposit payment failed" in subjects
    assert "Your booking is confirmed" not in subjects


def test_underpaid_deposit_sends_failure_notice(
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
    recorder = RecordingSender()
    app.dependency_overrides[get_notification_sender] = lambda: recorder

    booking = _approved_booking(
        client,
        customer_headers,
        admin_headers,
        seeded_branch,
        seeded_service,
        seeded_staff,
        seeded_shift,
        cleanup_records,
    )
    response = _webhook(client, booking["id"], 0.01)
    assert response.status_code == 400

    from app.webhooks.infrastructure.models import PaymentTransactionModel

    transaction = (
        db_session.query(PaymentTransactionModel)
        .filter(PaymentTransactionModel.booking_id == booking["id"])
        .first()
    )
    cleanup_records.append(("payment_transactions", transaction.id))

    subjects = [n.subject for n in recorder.sent]
    assert "Your deposit payment failed" in subjects
