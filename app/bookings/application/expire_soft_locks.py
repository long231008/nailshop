import logging
from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from app.audit_log.infrastructure.models import AuditLogModel
from app.bookings.infrastructure.models import BookingModel, BookingStatus
from app.webhooks.infrastructure.models import (
    PaymentTransactionModel,
    PaymentTransactionStatus,
    PaymentTransactionType,
)

logger = logging.getLogger(__name__)


def _has_paid_deposit(db: Session, booking_id: UUID) -> bool:
    return (
        db.query(PaymentTransactionModel)
        .filter(
            PaymentTransactionModel.booking_id == booking_id,
            PaymentTransactionModel.transaction_type == PaymentTransactionType.DEPOSIT,
            PaymentTransactionModel.status == PaymentTransactionStatus.SUCCESS,
        )
        .first()
        is not None
    )


def expire_unpaid_soft_locks(db: Session, redis_client: Redis) -> int:
    approved_bookings = (
        db.query(BookingModel).filter(BookingModel.status == BookingStatus.APPROVED).all()
    )

    expired_count = 0
    for booking in approved_bookings:
        if redis_client.exists(f"booking:soft_lock:{booking.id}"):
            continue

        if _has_paid_deposit(db, booking.id):
            continue

        booking.status = BookingStatus.CANCELLED
        db.add(
            AuditLogModel(
                actor_user_id=None,
                action="booking.soft_lock_expired",
                entity_type="booking",
                entity_id=booking.id,
                details={"reason": "deposit not received within the 15-minute soft-lock window"},
            )
        )
        expired_count += 1

    if expired_count:
        db.commit()
        logger.info("Auto-cancelled %d booking(s) after soft-lock expiry", expired_count)

    return expired_count
