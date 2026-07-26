"""Regression tests for the security/business-logic hardening pass."""

import hashlib
import hmac
import json
import uuid
from datetime import timedelta

from app.auth.domain.value_object import UserStatus
from app.auth.infrastructure.models import UserModel


def _sign(body: bytes) -> str:
    from app.shared.infrastructure.config.settings import settings

    return hmac.new(settings.PAYMENT_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


# --- OTP hardening -------------------------------------------------------------


def test_otp_burned_after_too_many_wrong_attempts(
    client, fake_redis, unique_phone, cleanup_identifiers
):
    cleanup_identifiers["phones"].append(unique_phone)
    pending_id = client.post("/app/auth/register", json={"phone_number": unique_phone}).json()[
        "pending_id"
    ]
    real_code = fake_redis.get(f"otp:register:{pending_id}")
    wrong_code = "000000" if real_code != "000000" else "111111"

    for _ in range(4):
        response = client.post(
            "/app/auth/verify", json={"pending_id": pending_id, "otp_code": wrong_code}
        )
        assert response.status_code == 400

    fifth = client.post("/app/auth/verify", json={"pending_id": pending_id, "otp_code": wrong_code})
    assert fifth.status_code == 429

    # The code is gone - even the correct one no longer works.
    with_real = client.post(
        "/app/auth/verify", json={"pending_id": pending_id, "otp_code": real_code}
    )
    assert with_real.status_code == 400


def test_blocked_user_cannot_use_existing_token(client, db_session, customer_identity):
    me_before = client.get("/app/me", headers=customer_identity["headers"])
    assert me_before.status_code == 200

    user = db_session.get(UserModel, customer_identity["id"])
    user.status = UserStatus.BLOCKED
    db_session.commit()

    me_after = client.get("/app/me", headers=customer_identity["headers"])
    assert me_after.status_code == 401


# --- Booking validation --------------------------------------------------------


def _booking_payload(seeded_branch, seeded_service, seeded_staff, start_time):
    return {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": start_time.isoformat(),
            }
        ],
    }


def test_booking_in_the_past_is_rejected(
    client, customer_headers, seeded_branch, seeded_service, seeded_staff, seeded_shift
):
    past = seeded_shift["start"] - timedelta(days=2)
    payload = _booking_payload(seeded_branch, seeded_service, seeded_staff, past)

    response = client.post("/app/bookings", json=payload, headers=customer_headers)

    assert response.status_code == 400


def test_booking_outside_opening_hours_is_rejected(
    client, customer_headers, seeded_branch, seeded_service, seeded_staff, seeded_shift
):
    after_hours = seeded_shift["end"] + timedelta(hours=1)
    payload = _booking_payload(seeded_branch, seeded_service, seeded_staff, after_hours)

    response = client.post("/app/bookings", json=payload, headers=customer_headers)

    assert response.status_code == 400


def test_booking_service_from_another_branch_is_rejected(
    client,
    customer_headers,
    db_session,
    cleanup_records,
    seeded_branch,
    seeded_staff,
    seeded_shift,
):
    from app.branches.infrastructure.models import LocationModel
    from app.services.infrastructure.models import ServiceModel

    other_branch = LocationModel(name=f"Other-{uuid.uuid4().hex[:6]}")
    db_session.add(other_branch)
    db_session.flush()
    foreign_service = ServiceModel(
        branch_id=other_branch.id,
        name="Foreign Service",
        category="gel",
        duration_min=30,
        base_price=25.0,
    )
    db_session.add(foreign_service)
    db_session.commit()
    cleanup_records.append(("locations", other_branch.id))
    cleanup_records.append(("services", foreign_service.id))

    payload = _booking_payload(
        seeded_branch, foreign_service.id, seeded_staff, seeded_shift["start"]
    )

    response = client.post("/app/bookings", json=payload, headers=customer_headers)

    assert response.status_code == 400


def test_customer_can_cancel_pending_booking_with_notice(
    client,
    customer_headers,
    other_customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    payload = _booking_payload(seeded_branch, seeded_service, seeded_staff, seeded_shift["start"])
    booking = client.post("/app/bookings", json=payload, headers=customer_headers).json()
    cleanup_records.append(("bookings", booking["id"]))
    cleanup_records.append(("booking_details", booking["details"][0]["id"]))

    # Someone else's token cannot cancel it.
    foreign = client.post(f"/app/bookings/{booking['id']}/cancel", headers=other_customer_headers)
    assert foreign.status_code == 403

    response = client.post(f"/app/bookings/{booking['id']}/cancel", headers=customer_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    # And a cancelled booking cannot be completed by mistake.
    admin_complete = client.post(f"/app/bookings/{booking['id']}/cancel", headers=customer_headers)
    assert admin_complete.status_code == 400


def test_cancelled_booking_cannot_be_completed_manually(
    client,
    admin_headers,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    payload = _booking_payload(seeded_branch, seeded_service, seeded_staff, seeded_shift["start"])
    booking = client.post("/app/bookings", json=payload, headers=customer_headers).json()
    cleanup_records.append(("bookings", booking["id"]))
    cleanup_records.append(("booking_details", booking["details"][0]["id"]))

    client.post(f"/app/bookings/{booking['id']}/cancel", headers=customer_headers)

    response = client.post(f"/app/bookings/{booking['id']}/complete-manual", headers=admin_headers)
    assert response.status_code == 400


def test_design_quote_cannot_underprice_another_service(
    client,
    customer_headers,
    customer_identity,
    db_session,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    """A £3 nail art quote must not buy a £20 manicure."""
    from app.custom_designs.infrastructure.models import CustomDesignModel, CustomDesignStatus

    design = CustomDesignModel(
        customer_id=customer_identity["id"],
        description="cheap art",
        estimated_price=3.0,
        status=CustomDesignStatus.PRICED,
    )
    db_session.add(design)
    db_session.commit()
    cleanup_records.append(("custom_designs", design.id))

    response = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    # seeded_service is a manicure, not nail art.
                    "service_id": str(seeded_service),
                    "staff_id": str(seeded_staff["staff_id"]),
                    "start_time": seeded_shift["start"].isoformat(),
                    "custom_design_id": str(design.id),
                }
            ],
        },
        headers=customer_headers,
    )

    assert response.status_code == 400
    db_session.refresh(design)
    assert design.status == CustomDesignStatus.PRICED


def test_oversized_design_upload_is_rejected(client, customer_headers):
    import io

    from app.custom_designs.presentation.routers import MAX_UPLOAD_BYTES

    oversized = io.BytesIO(b"\x00" * (MAX_UPLOAD_BYTES + 1024))
    response = client.post(
        "/app/custom-designs",
        headers=customer_headers,
        files={"file": ("huge.png", oversized, "image/png")},
    )

    assert response.status_code == 413


# --- Payment webhook -----------------------------------------------------------


def test_webhook_underpayment_does_not_unlock_booking(
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
    payload = _booking_payload(seeded_branch, seeded_service, seeded_staff, seeded_shift["start"])
    booking = client.post("/app/bookings", json=payload, headers=customer_headers).json()
    cleanup_records.append(("bookings", booking["id"]))
    cleanup_records.append(("booking_details", booking["details"][0]["id"]))
    client.post(f"/app/bookings/{booking['id']}/approve", headers=admin_headers)

    transaction_id = str(uuid.uuid4())
    body = json.dumps(
        {
            "transaction_id": transaction_id,
            "booking_id": booking["id"],
            "amount": 0.01,
            "transaction_type": "deposit",
        }
    ).encode()

    response = client.post(
        "/app/webhooks/payment",
        content=body,
        headers={"X-Signature": _sign(body), "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    # The soft lock survives: the booking is still awaiting its real deposit.
    assert fake_redis.get(f"booking:soft_lock:{booking['id']}") is not None

    from app.webhooks.infrastructure.models import (
        PaymentTransactionModel,
        PaymentTransactionStatus,
    )

    transaction = (
        db_session.query(PaymentTransactionModel)
        .filter(PaymentTransactionModel.provider_transaction_id == transaction_id)
        .first()
    )
    assert transaction is not None
    assert transaction.status == PaymentTransactionStatus.FAILED
    cleanup_records.append(("payment_transactions", transaction.id))


def test_webhook_failed_payment_is_recorded_but_not_counted(
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
    payload = _booking_payload(seeded_branch, seeded_service, seeded_staff, seeded_shift["start"])
    booking = client.post("/app/bookings", json=payload, headers=customer_headers).json()
    cleanup_records.append(("bookings", booking["id"]))
    cleanup_records.append(("booking_details", booking["details"][0]["id"]))
    client.post(f"/app/bookings/{booking['id']}/approve", headers=admin_headers)

    transaction_id = str(uuid.uuid4())
    body = json.dumps(
        {
            "transaction_id": transaction_id,
            "booking_id": booking["id"],
            "amount": 6.0,
            "transaction_type": "deposit",
            "status": "failed",
        }
    ).encode()

    response = client.post(
        "/app/webhooks/payment",
        content=body,
        headers={"X-Signature": _sign(body), "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["processed"] is False
    assert fake_redis.get(f"booking:soft_lock:{booking['id']}") is not None

    from app.webhooks.infrastructure.models import PaymentTransactionModel

    transaction = (
        db_session.query(PaymentTransactionModel)
        .filter(PaymentTransactionModel.provider_transaction_id == transaction_id)
        .first()
    )
    cleanup_records.append(("payment_transactions", transaction.id))


# --- Availability --------------------------------------------------------------


def test_availability_never_offers_past_slots(client, seeded_staff, seeded_service):
    from datetime import datetime, timezone

    from app.shared.infrastructure.clock import is_closed_day, shop_timezone, today_in_shop_tz

    now = datetime.now(timezone.utc)
    response = client.get(
        "/app/availability",
        params={
            "branch_id": str(seeded_staff["branch_id"]),
            "service_id": str(seeded_service),
            "date": today_in_shop_tz().isoformat(),
        },
    )

    assert response.status_code == 200
    slots = response.json()
    for slot in slots:
        start = datetime.fromisoformat(slot["start_time"])
        assert start > now
    # Only assert there IS availability while a good chunk of the day remains -
    # after closing time (or on a closed Sunday) an empty list is the correct answer.
    if now.astimezone(shop_timezone()).hour < 16 and not is_closed_day(today_in_shop_tz()):
        assert slots, "expected some availability for the rest of the day"
