from uuid import UUID

from sqlalchemy.orm import Session

from app.audit_log.infrastructure.models import AuditLogModel
from app.bookings.application.exceptions import BookingNotFoundError
from app.bookings.infrastructure.models import (
    BookingDetailModel,
    BookingDetailStatus,
    BookingModel,
    BookingStatus,
)


def complete_booking_manually(db: Session, booking_id: UUID, actor_user_id: UUID) -> BookingModel:
    booking = db.get(BookingModel, booking_id)
    if booking is None:
        raise BookingNotFoundError()

    db.query(BookingDetailModel).filter(BookingDetailModel.booking_id == booking_id).update(
        {"status": BookingDetailStatus.COMPLETED}
    )
    booking.status = BookingStatus.COMPLETED
    db.add(
        AuditLogModel(
            actor_user_id=actor_user_id,
            action="booking.complete_manual",
            entity_type="booking",
            entity_id=booking.id,
        )
    )
    db.commit()
    db.refresh(booking)
    return booking
