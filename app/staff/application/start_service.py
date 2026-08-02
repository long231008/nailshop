from uuid import UUID

from sqlalchemy.orm import Session

from app.bookings.infrastructure.models import (
    BookingDetailModel,
    BookingDetailStatus,
    BookingModel,
    BookingStatus,
)
from app.shared.infrastructure.clock import day_bounds_utc, today_in_shop_tz


class BookingDetailNotFoundError(Exception):
    pass


class StaffBusyError(Exception):
    pass


class BookingNotStartableError(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


def start_service(db: Session, staff_id: UUID, booking_detail_id: UUID) -> BookingDetailModel:
    detail = db.get(BookingDetailModel, booking_detail_id)
    if detail is None or detail.staff_id != staff_id:
        raise BookingDetailNotFoundError()

    # Only a live, salon-approved booking may be started - a PENDING one has
    # not been granted, a cancelled or no-show one no longer exists for the
    # floor.
    booking = db.get(BookingModel, detail.booking_id)
    if booking is None or booking.status not in (
        BookingStatus.APPROVED,
        BookingStatus.IN_PROGRESS,
    ):
        raise BookingNotStartableError("This booking has not been approved yet")

    # A tech starts a service when the customer sits down, which is always
    # "today" - starting next week's leg by accident must not be possible.
    day_start, day_end = day_bounds_utc(today_in_shop_tz())
    if not (day_start <= detail.start_time < day_end):
        raise BookingNotStartableError("This appointment is not scheduled for today")

    # One customer at a time - but only today's work counts. A leg someone
    # forgot to complete on a previous day must not block the tech forever.
    busy = (
        db.query(BookingDetailModel)
        .filter(
            BookingDetailModel.staff_id == staff_id,
            BookingDetailModel.status == BookingDetailStatus.IN_PROGRESS,
            BookingDetailModel.start_time >= day_start,
            BookingDetailModel.start_time < day_end,
        )
        .first()
    )
    if busy is not None:
        raise StaffBusyError()

    detail.status = BookingDetailStatus.IN_PROGRESS

    if booking.status == BookingStatus.APPROVED:
        booking.status = BookingStatus.IN_PROGRESS

    db.commit()
    db.refresh(detail)
    return detail
