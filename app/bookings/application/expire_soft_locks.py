import logging
from datetime import timedelta
from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from app.audit_log.infrastructure.models import AuditLogModel
from app.bookings.application.approve import SOFT_LOCK_TTL_SECONDS
from app.bookings.application.cancel import release_designs
from app.bookings.infrastructure.models import BookingModel, BookingStatus
from app.notification.application.notify import notify_booking_cancelled
from app.notification.domain.sender import NotificationSender
from app.shared.infrastructure.clock import now_utc
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


def expire_unpaid_soft_locks(
    db: Session, redis_client: Redis, notification_sender: NotificationSender | None = None
) -> int:
    """Release approved bookings whose deposit window has passed.

    The decision is made from `approved_at` in the database - the Redis marker is
    only a fast-path hint, so losing Redis data can never cancel a paid booking
    early. Bookings approved before the column existed (approved_at is NULL) are
    left alone.
    """
    deadline = now_utc() - timedelta(seconds=SOFT_LOCK_TTL_SECONDS)
    expired_bookings = (
        db.query(BookingModel)
        .filter(
            BookingModel.status == BookingStatus.APPROVED,
            BookingModel.approved_at.isnot(None),
            BookingModel.approved_at < deadline,
        )
        .all()
    )

    expired_count = 0
    for booking in expired_bookings:
        if _has_paid_deposit(db, booking.id):
            continue

        booking.status = BookingStatus.CANCELLED
        release_designs(db, booking.id)
        redis_client.delete(f"booking:soft_lock:{booking.id}")
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

        if notification_sender is not None:
            notify_booking_cancelled(notification_sender, db, booking.customer_id)

    if expired_count:
        db.commit()
        logger.info("Auto-cancelled %d booking(s) after soft-lock expiry", expired_count)

    return expired_count
