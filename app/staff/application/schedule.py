from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.shifts.infrastructure.models import StaffRosterModel

SCHEDULE_HORIZON_DAYS = 14


def get_staff_schedule(db: Session, staff_id: UUID) -> dict:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=SCHEDULE_HORIZON_DAYS)

    shifts = (
        db.query(StaffRosterModel)
        .filter(
            StaffRosterModel.staff_id == staff_id,
            StaffRosterModel.end_time >= now,
            StaffRosterModel.start_time <= horizon,
        )
        .order_by(StaffRosterModel.start_time)
        .all()
    )

    appointments = (
        db.query(BookingDetailModel)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .filter(
            BookingDetailModel.staff_id == staff_id,
            BookingModel.status.notin_([BookingStatus.CANCELLED, BookingStatus.NO_SHOW]),
            BookingDetailModel.end_time >= now,
            BookingDetailModel.start_time <= horizon,
        )
        .order_by(BookingDetailModel.start_time)
        .all()
    )

    return {"shifts": shifts, "appointments": appointments}
