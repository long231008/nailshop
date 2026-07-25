from uuid import UUID

from sqlalchemy.orm import Session

from app.bookings.application.exceptions import BookingDetailNotFoundError, BookingNotFoundError
from app.bookings.infrastructure.models import (
    BookingDetailModel,
    BookingDetailStatus,
    BookingModel,
    BookingStatus,
)


def complete_booking_detail(db: Session, booking_id: UUID, booking_detail_id: UUID) -> BookingModel:
    booking = db.get(BookingModel, booking_id)
    if booking is None:
        raise BookingNotFoundError()

    detail = db.get(BookingDetailModel, booking_detail_id)
    if detail is None or detail.booking_id != booking_id:
        raise BookingDetailNotFoundError()

    detail.status = BookingDetailStatus.COMPLETED
    db.flush()

    all_details = (
        db.query(BookingDetailModel).filter(BookingDetailModel.booking_id == booking_id).all()
    )
    if all(d.status == BookingDetailStatus.COMPLETED for d in all_details):
        booking.status = BookingStatus.COMPLETED

    db.commit()
    db.refresh(booking)
    return booking
