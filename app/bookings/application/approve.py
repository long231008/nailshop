import logging
from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from app.bookings.application.exceptions import BookingNotFoundError, InvalidBookingStateError
from app.bookings.infrastructure.models import BookingModel, BookingStatus
from app.shared.infrastructure.config.settings import settings

logger = logging.getLogger(__name__)

DEPOSIT_PERCENTAGE = 0.3
SOFT_LOCK_TTL_SECONDS = 15 * 60


def approve_booking(db: Session, redis_client: Redis, booking_id: UUID) -> tuple[BookingModel, str]:
    booking = db.get(BookingModel, booking_id)
    if booking is None:
        raise BookingNotFoundError()
    if booking.status != BookingStatus.PENDING:
        raise InvalidBookingStateError("Only pending bookings can be approved")

    deposit_amount = round(float(booking.total_price or 0) * DEPOSIT_PERCENTAGE, 2)
    booking.status = BookingStatus.APPROVED
    booking.deposit_amount = deposit_amount
    db.commit()
    db.refresh(booking)

    redis_client.set(
        f"booking:soft_lock:{booking.id}", "awaiting_deposit", ex=SOFT_LOCK_TTL_SECONDS
    )

    deposit_link = f"{settings.PAYMENT_CHECKOUT_BASE_URL}/{booking.id}?amount={deposit_amount}"

    logger.info(
        "Booking %s approved for customer %s, deposit link: %s",
        booking.id,
        booking.customer_id,
        deposit_link,
    )

    return booking, deposit_link
