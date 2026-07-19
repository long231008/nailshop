from uuid import UUID

from sqlalchemy.orm import Session

from app.bookings.infrastructure.models import (
    BookingDetailModel,
    BookingDetailStatus,
    BookingModel,
    BookingStatus,
)


class BookingDetailNotFoundError(Exception):
    pass


class StaffBusyError(Exception):
    pass


def start_service(db: Session, staff_id: UUID, booking_detail_id: UUID) -> BookingDetailModel:
    detail = db.get(BookingDetailModel, booking_detail_id)
    if detail is None or detail.staff_id != staff_id:
        raise BookingDetailNotFoundError()

    busy = (
        db.query(BookingDetailModel)
        .filter(
            BookingDetailModel.staff_id == staff_id,
            BookingDetailModel.status == BookingDetailStatus.IN_PROGRESS,
        )
        .first()
    )
    if busy is not None:
        raise StaffBusyError()

    detail.status = BookingDetailStatus.IN_PROGRESS

    booking = db.get(BookingModel, detail.booking_id)
    if booking.status == BookingStatus.APPROVED:
        booking.status = BookingStatus.IN_PROGRESS

    db.commit()
    db.refresh(detail)
    return detail
