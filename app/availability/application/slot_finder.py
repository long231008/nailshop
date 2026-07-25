from datetime import date as date_type
from datetime import timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.services.infrastructure.models import ServiceExtensionModel, ServiceModel
from app.shared.infrastructure.clock import day_bounds_utc, now_utc
from app.shifts.infrastructure.models import StaffRosterModel
from app.staff.infrastructure.models import StaffModel, StaffStatus

BUFFER_MINUTES = 15
SLOT_STEP_MINUTES = 15
# Nobody can book a slot that starts in the next few minutes anyway.
MIN_LEAD_MINUTES = 15


class ServiceNotFoundError(Exception):
    pass


def find_available_slots(
    db: Session,
    branch_id: UUID,
    service_id: UUID,
    target_date: date_type,
    staff_id: UUID | None = None,
    service_extension_id: UUID | None = None,
) -> list[dict]:
    service = db.get(ServiceModel, service_id)
    if service is None:
        raise ServiceNotFoundError()

    duration_min = service.duration_min
    if service_extension_id is not None:
        extension = db.get(ServiceExtensionModel, service_extension_id)
        if extension is not None:
            duration_min += extension.extra_duration_min

    staff_query = db.query(StaffModel).filter(
        StaffModel.branch_id == branch_id, StaffModel.status == StaffStatus.ACTIVE
    )
    if staff_id is not None:
        staff_query = staff_query.filter(StaffModel.id == staff_id)
    staff_list = staff_query.all()

    day_start, day_end = day_bounds_utc(target_date)
    # Past slots are never offered: today's window starts at "now + lead time".
    earliest_start = max(day_start, now_utc() + timedelta(minutes=MIN_LEAD_MINUTES))

    slots = []
    for staff in staff_list:
        shifts = (
            db.query(StaffRosterModel)
            .filter(
                StaffRosterModel.staff_id == staff.id,
                StaffRosterModel.start_time < day_end,
                StaffRosterModel.end_time > day_start,
            )
            .all()
        )
        if not shifts:
            continue

        busy_details = (
            db.query(BookingDetailModel)
            .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
            .filter(
                BookingDetailModel.staff_id == staff.id,
                BookingModel.status.notin_([BookingStatus.CANCELLED, BookingStatus.NO_SHOW]),
                BookingDetailModel.start_time < day_end,
                BookingDetailModel.end_time > day_start,
            )
            .order_by(BookingDetailModel.start_time)
            .all()
        )

        for shift in shifts:
            window_start = max(shift.start_time, earliest_start)
            window_end = min(shift.end_time, day_end)
            if window_start >= window_end:
                continue
            cursor = window_start
            free_windows = []

            for detail in busy_details:
                busy_start = detail.start_time - timedelta(minutes=BUFFER_MINUTES)
                busy_end = detail.end_time + timedelta(minutes=BUFFER_MINUTES)
                if busy_end <= cursor or busy_start >= window_end:
                    continue
                if busy_start > cursor:
                    free_windows.append((cursor, min(busy_start, window_end)))
                cursor = max(cursor, busy_end)

            if cursor < window_end:
                free_windows.append((cursor, window_end))

            for win_start, win_end in free_windows:
                slot_start = win_start
                while slot_start + timedelta(minutes=duration_min) <= win_end:
                    slots.append(
                        {
                            "staff_id": staff.id,
                            "start_time": slot_start,
                            "end_time": slot_start + timedelta(minutes=duration_min),
                        }
                    )
                    slot_start += timedelta(minutes=SLOT_STEP_MINUTES)

    slots.sort(key=lambda s: s["start_time"])
    return slots
