import uuid
from datetime import date

from app.audit_log.infrastructure.models import AuditLogModel
from app.bookings.application.expire_soft_locks import expire_unpaid_soft_locks
from app.bookings.infrastructure.models import BookingModel, BookingStatus
from app.webhooks.infrastructure.models import (
    PaymentTransactionModel,
    PaymentTransactionStatus,
    PaymentTransactionType,
)


def _create_booking(db_session, cleanup_records, customer_id, branch_id, status):
    booking = BookingModel(
        customer_id=customer_id,
        branch_id=branch_id,
        booking_date=date.today(),
        status=status,
        total_price=20.0,
        deposit_amount=6.0,
    )
    db_session.add(booking)
    db_session.commit()
    cleanup_records.append(("bookings", booking.id))
    return booking


def test_expires_approved_booking_without_soft_lock_or_payment(
    db_session, fake_redis, cleanup_records, customer_identity, seeded_branch
):
    booking = _create_booking(
        db_session, cleanup_records, customer_identity["id"], seeded_branch,
        BookingStatus.APPROVED,
    )

    expired_count = expire_unpaid_soft_locks(db_session, fake_redis)

    assert expired_count == 1
    db_session.refresh(booking)
    assert booking.status == BookingStatus.CANCELLED

    log_entry = (
        db_session.query(AuditLogModel)
        .filter(
            AuditLogModel.entity_id == booking.id,
            AuditLogModel.action == "booking.soft_lock_expired",
        )
        .first()
    )
    assert log_entry is not None
    cleanup_records.append(("audit_logs", log_entry.id))


def test_does_not_expire_booking_while_soft_lock_still_active(
    db_session, fake_redis, cleanup_records, customer_identity, seeded_branch
):
    booking = _create_booking(
        db_session, cleanup_records, customer_identity["id"], seeded_branch,
        BookingStatus.APPROVED,
    )
    fake_redis.set(f"booking:soft_lock:{booking.id}", "awaiting_deposit", ex=900)

    expired_count = expire_unpaid_soft_locks(db_session, fake_redis)

    assert expired_count == 0
    db_session.refresh(booking)
    assert booking.status == BookingStatus.APPROVED


def test_does_not_expire_booking_with_successful_deposit(
    db_session, fake_redis, cleanup_records, customer_identity, seeded_branch
):
    booking = _create_booking(
        db_session, cleanup_records, customer_identity["id"], seeded_branch,
        BookingStatus.APPROVED,
    )
    transaction = PaymentTransactionModel(
        booking_id=booking.id,
        provider_transaction_id=str(uuid.uuid4()),
        amount=6.0,
        transaction_type=PaymentTransactionType.DEPOSIT,
        status=PaymentTransactionStatus.SUCCESS,
    )
    db_session.add(transaction)
    db_session.commit()
    cleanup_records.append(("payment_transactions", transaction.id))

    expired_count = expire_unpaid_soft_locks(db_session, fake_redis)

    assert expired_count == 0
    db_session.refresh(booking)
    assert booking.status == BookingStatus.APPROVED


def test_ignores_non_approved_bookings(
    db_session, fake_redis, cleanup_records, customer_identity, seeded_branch
):
    booking = _create_booking(
        db_session, cleanup_records, customer_identity["id"], seeded_branch,
        BookingStatus.PENDING,
    )

    expired_count = expire_unpaid_soft_locks(db_session, fake_redis)

    assert expired_count == 0
    db_session.refresh(booking)
    assert booking.status == BookingStatus.PENDING
