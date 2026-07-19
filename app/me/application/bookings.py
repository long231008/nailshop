from uuid import UUID

from sqlalchemy.orm import Session

from app.bookings.infrastructure.models import BookingModel


def list_my_bookings(db: Session, customer_id: UUID) -> list[BookingModel]:
    return (
        db.query(BookingModel)
        .filter(BookingModel.customer_id == customer_id)
        .order_by(BookingModel.created_at.desc())
        .all()
    )
