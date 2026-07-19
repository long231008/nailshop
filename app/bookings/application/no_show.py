from uuid import UUID

from sqlalchemy.orm import Session

from app.audit_log.infrastructure.models import AuditLogModel
from app.bookings.application.exceptions import BookingNotFoundError
from app.bookings.infrastructure.models import BookingModel, BookingStatus


def mark_no_show(db: Session, booking_id: UUID, actor_user_id: UUID) -> BookingModel:
    booking = db.get(BookingModel, booking_id)
    if booking is None:
        raise BookingNotFoundError()

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
