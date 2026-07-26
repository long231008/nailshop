from datetime import date as date_type
from datetime import timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.services.infrastructure.models import ServiceExtensionModel, ServiceModel
from app.shared.infrastructure.clock import (
    is_closed_day,
    last_booking_utc,
    now_utc,
    opening_window_utc,
    within_booking_horizon,
)
from app.slot_locks.application.locks import locks_overlapping
from app.staff.infrastructure.models import StaffModel, StaffStatus

BUFFER_MINUTES = 15
SLOT_STEP_MINUTES = 15
# Nobody can book a slot that starts in the next few minutes anyway.
MIN_LEAD_MINUTES = 15


class ServiceNotFoundError(Exception):
    pass


def _align_to_grid(moment):
    """Round up to the next quarter-hour so offered times read 09:00, 09:15, ..."""
    if (moment.minute % SLOT_STEP_MINUTES, moment.second, moment.microsecond) == (0, 0, 0):
        return moment
    floored = moment.replace(
        minute=moment.minute - moment.minute % SLOT_STEP_MINUTES, second=0, microsecond=0
    )
    return floored + timedelta(minutes=SLOT_STEP_MINUTES)


def find_available_slots(
    db: Session,
    branch_id: UUID,
    service_id: UUID,
    target_date: date_type,
    staff_id: UUID | None = None,
    service_extension_id: UUID | None = None,
    duration_min: int | None = None,
) -> list[dict]:
    """Free slots inside the shop's opening hours.

    The calendar is open every day within the booking horizon; what limits it is
    existing bookings and explicit slot locks, not rosters.
    """
    service = db.get(ServiceModel, service_id)
    if service is None:
        raise ServiceNotFoundError()

    if not within_booking_horizon(target_date) or is_closed_day(target_date):
        return []

    if duration_min is None:
        # A cart of several services passes its combined duration instead.
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

    open_utc, _close_utc = opening_window_utc(target_date)
    last_start = last_booking_utc(target_date)
    # A treatment starting at the last accepted time may run past closing, so the
    # working span extends to cover it.
    span_end = last_start + timedelta(minutes=duration_min)
    # Past slots are never offered: today's window starts at "now + lead time".
    earliest_start = max(open_utc, now_utc() + timedelta(minutes=MIN_LEAD_MINUTES))
    if earliest_start > last_start:
        return []

    locks = locks_overlapping(db, branch_id, open_utc, span_end)

    slots = []
    for staff in staff_list:
        busy_windows = []

        details = (
            db.query(BookingDetailModel)
            .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
            .filter(
                BookingDetailModel.staff_id == staff.id,
                BookingModel.status.notin_([BookingStatus.CANCELLED, BookingStatus.NO_SHOW]),
                BookingDetailModel.start_time < span_end,
                BookingDetailModel.end_time > open_utc,
            )
            .all()
        )
        for detail in details:
            busy_windows.append(
                (
                    detail.start_time - timedelta(minutes=BUFFER_MINUTES),
                    detail.end_time + timedelta(minutes=BUFFER_MINUTES),
                )
            )
        # Locks close the slot exactly as published - no extra buffer around them.
        for lock in locks:
            if lock.staff_id is None or lock.staff_id == staff.id:
                busy_windows.append((lock.start_time, lock.end_time))

        busy_windows.sort()

        cursor = earliest_start
        free_windows = []
        for busy_start, busy_end in busy_windows:
            if busy_end <= cursor or busy_start >= span_end:
                continue
            if busy_start > cursor:
                free_windows.append((cursor, min(busy_start, span_end)))
            cursor = max(cursor, busy_end)
        if cursor < span_end:
            free_windows.append((cursor, span_end))

        for win_start, win_end in free_windows:
            slot_start = _align_to_grid(win_start)
            while (
                slot_start <= last_start and slot_start + timedelta(minutes=duration_min) <= win_end
            ):
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
