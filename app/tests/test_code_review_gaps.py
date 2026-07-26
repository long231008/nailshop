import io
import uuid
from datetime import datetime, timezone

from fastapi import status

from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.jwt_provider import JwtTokenProvider
from app.auth.infrastructure.models import UserModel
from app.bookings.application.expire_soft_locks import expire_unpaid_soft_locks
from app.bookings.infrastructure.models import BookingModel, BookingStatus
from app.webhooks.application.payment import process_payment_webhook
from app.webhooks.infrastructure.models import PaymentTransactionType


def test_gap_1_custom_design_upload_rejects_non_image_file(
    client, customer_headers, cleanup_records
):
    """REVIEW FINDING: Upload endpoint should reject non-image file types (e.g. .sh, .exe)."""
    file_data = io.BytesIO(b"echo 'malicious script'")
    files = {"file": ("script.sh", file_data, "text/x-shellscript")}

    response = client.post("/app/custom-designs", headers=customer_headers, files=files)

    if response.status_code == status.HTTP_201_CREATED:
        design_id = response.json()["id"]
        cleanup_records.append(("custom_designs", design_id))

    # EXPECTATION FOR PR REVIEW: Should return 400 Bad Request
    # CURRENT FLOW FAIL: Returns 201 Created because MIME type is not validated
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_gap_2_jwt_auth_rejects_inactive_or_pending_user(client, db_session, cleanup_records):
    """REVIEW FINDING: get_current_user should verify user status in DB and reject pending/inactive accounts."""
    pending_user = UserModel(
        phone_number=f"09{uuid.uuid4().int % 10**8:08d}",
        status=UserStatus.PENDING,
        role=UserRole.CUSTOMER,
    )
    db_session.add(pending_user)
    db_session.commit()
    cleanup_records.append(("users", pending_user.id))

    token = JwtTokenProvider().create_access_token(user_id=pending_user.id, role=UserRole.CUSTOMER)
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/app/me/bookings", headers=headers)

    # EXPECTATION FOR PR REVIEW: Should return 401 Unauthorized for pending/inactive user
    # CURRENT FLOW FAIL: Returns 200 OK because JWT claims are trusted without DB status check
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_gap_3_expire_soft_locks_safeguard_when_redis_key_lost(
    db_session, fake_redis, seeded_branch, cleanup_records
):
    """REVIEW FINDING: expire_unpaid_soft_locks should not cancel freshly approved bookings if Redis key is missing."""
    user = UserModel(
        phone_number=f"09{uuid.uuid4().int % 10**8:08d}",
        status=UserStatus.ACTIVE,
        role=UserRole.CUSTOMER,
    )
    db_session.add(user)
    db_session.flush()

    booking = BookingModel(
        customer_id=user.id,
        branch_id=seeded_branch,
        booking_date=datetime.now(timezone.utc).date(),
        status=BookingStatus.APPROVED,
        total_price=50.0,
        deposit_amount=15.0,
        updated_at=datetime.now(timezone.utc),  # Approved 1 second ago
    )
    db_session.add(booking)
    db_session.commit()

    cleanup_records.append(("bookings", booking.id))

    # Simulate Redis cache flush / missing key
    fake_redis.delete(f"booking:soft_lock:{booking.id}")

    # Run soft lock expiration job
    expire_unpaid_soft_locks(db_session, fake_redis)

    db_session.refresh(booking)
    # EXPECTATION FOR PR REVIEW: Should remain APPROVED because 15 minutes have not passed
    # CURRENT FLOW FAIL: Status becomes CANCELLED because it solely relies on Redis key existence
    assert booking.status == BookingStatus.APPROVED


def test_gap_4_payment_webhook_updates_pending_booking_status(
    db_session, seeded_branch, cleanup_records
):
    """REVIEW FINDING: process_payment_webhook should update booking status when deposit payment succeeds."""
    user = UserModel(
        phone_number=f"09{uuid.uuid4().int % 10**8:08d}",
        status=UserStatus.ACTIVE,
        role=UserRole.CUSTOMER,
    )
    db_session.add(user)
    db_session.flush()

    booking = BookingModel(
        customer_id=user.id,
        branch_id=seeded_branch,
        booking_date=datetime.now(timezone.utc).date(),
        status=BookingStatus.PENDING,
        total_price=100.0,
        deposit_amount=30.0,
    )
    db_session.add(booking)
    db_session.commit()

    # Append booking first, then payment_transaction second (so reversed cleanup deletes tx before booking)
    cleanup_records.append(("bookings", booking.id))

    tx_id = f"tx_{uuid.uuid4().hex[:8]}"
    tx, _ = process_payment_webhook(
        db_session,
        transaction_id=tx_id,
        booking_id=booking.id,
        amount=30.0,
        transaction_type=PaymentTransactionType.DEPOSIT,
        succeeded=True,
    )

    cleanup_records.append(("payment_transactions", tx.id))

    db_session.refresh(booking)
    # EXPECTATION FOR PR REVIEW: Booking status should update to APPROVED upon successful deposit
    # CURRENT FLOW FAIL: Status remains PENDING because process_payment_webhook does not update booking.status
    assert booking.status == BookingStatus.APPROVED
