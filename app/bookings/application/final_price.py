from uuid import UUID

from sqlalchemy.orm import Session

from app.bookings.application.exceptions import BookingNotFoundError, InvalidBookingStateError
from app.bookings.infrastructure.models import BookingModel, BookingStatus


def set_final_price(db: Session, booking_id: UUID, final_price: float) -> BookingModel:
    booking = db.get(BookingModel, booking_id)
    if booking is None:
        raise BookingNotFoundError()
    if booking.status != BookingStatus.COMPLETED:
        raise InvalidBookingStateError(
            "Final price can only be set after the booking is completed"
        )

    booking.final_price = final_price
    db.commit()
    db.refresh(booking)
    return booking
