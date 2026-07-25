from uuid import UUID

from sqlalchemy.orm import Session

from app.audit_log.infrastructure.models import AuditLogModel
from app.bookings.application.exceptions import BookingNotFoundError, InvalidBookingStateError
from app.bookings.infrastructure.models import BookingModel, BookingStatus

NO_SHOWABLE_STATUSES = (BookingStatus.PENDING, BookingStatus.APPROVED)


def mark_no_show(db: Session, booking_id: UUID, actor_user_id: UUID) -> BookingModel:
    booking = db.get(BookingModel, booking_id)
    if booking is None:
        raise BookingNotFoundError()
    if booking.status not in NO_SHOWABLE_STATUSES:
        raise InvalidBookingStateError("Only pending or approved bookings can be marked as no-show")

    booking.status = BookingStatus.NO_SHOW
    db.add(
        AuditLogModel(
            actor_user_id=actor_user_id,
            action="booking.no_show",
            entity_type="booking",
            entity_id=booking.id,
        )
    )
    db.commit()
    db.refresh(booking)
    return booking
