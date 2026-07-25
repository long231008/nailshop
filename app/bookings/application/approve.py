import logging
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from app.audit_log.infrastructure.models import AuditLogModel
from app.bookings.application.exceptions import BookingNotFoundError, InvalidBookingStateError
from app.bookings.infrastructure.models import BookingModel, BookingStatus
from app.notification.application.notify import notify_deposit_request
from app.notification.domain.sender import NotificationSender
from app.shared.infrastructure.clock import now_utc
from app.shared.infrastructure.config.settings import settings

logger = logging.getLogger(__name__)

DEPOSIT_PERCENTAGE = Decimal("0.3")
SOFT_LOCK_TTL_SECONDS = 15 * 60


def approve_booking(
    db: Session,
    redis_client: Redis,
    booking_id: UUID,
    actor_user_id: UUID,
    notification_sender: NotificationSender,
) -> tuple[BookingModel, str]:
    booking = db.get(BookingModel, booking_id)
    if booking is None:
        raise BookingNotFoundError()
    if booking.status != BookingStatus.PENDING:
        raise InvalidBookingStateError("Only pending bookings can be approved")

    total = Decimal(str(booking.total_price or 0))
    deposit_amount = (total * DEPOSIT_PERCENTAGE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    booking.status = BookingStatus.APPROVED
    booking.deposit_amount = deposit_amount
    booking.approved_at = now_utc()
    db.add(
        AuditLogModel(
            actor_user_id=actor_user_id,
            action="booking.approved",
            entity_type="booking",
            entity_id=booking.id,
            details={"deposit_amount": str(deposit_amount)},
        )
    )
    db.commit()
    db.refresh(booking)

    # Fast-path marker only; the expiry job decides from approved_at in the database.
    redis_client.set(
        f"booking:soft_lock:{booking.id}", "awaiting_deposit", ex=SOFT_LOCK_TTL_SECONDS
    )

    deposit_link = f"{settings.PAYMENT_CHECKOUT_BASE_URL}/{booking.id}?amount={deposit_amount}"

    notify_deposit_request(
        notification_sender,
        db,
        booking.customer_id,
        float(deposit_amount),
        deposit_link,
        SOFT_LOCK_TTL_SECONDS,
    )

    logger.info("Booking %s approved for customer %s", booking.id, booking.customer_id)

    return booking, deposit_link
