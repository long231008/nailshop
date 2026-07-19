from uuid import UUID

from sqlalchemy.orm import Session

from app.bookings.application.exceptions import BookingNotFoundError
from app.bookings.infrastructure.models import BookingModel


def get_bill(db: Session, booking_id: UUID) -> dict:
    booking = db.get(BookingModel, booking_id)
    if booking is None:
        raise BookingNotFoundError()

    total = float(
        booking.final_price if booking.final_price is not None else (booking.total_price or 0)
    )
    deposit = float(booking.deposit_amount or 0)
    return {"id": booking.id, "total": total, "deposit": deposit, "remaining": total - deposit}
