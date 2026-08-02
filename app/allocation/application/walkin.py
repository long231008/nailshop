"""Intraday walk-in engine (doc 3.3/3.6, trimmed to what reception needs).

Today's schedule froze at last night's close; walk-ins are the only new
customers. Offer the earliest genuinely free seat per technician, ordered by
turn fairness, with a guard band before existing appointments so a slow walk-in
cannot eat into a promised booking.
"""

from datetime import timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.allocation.application.roster import expected_staff
from app.availability.application.capacity import (
    ACTIVE_BOOKING_STATUSES,
    matrix_configured_for,
    staff_timeline_busy,
)
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel
from app.capability.application.matrix import ceil_to_grid, load_matrix
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import (
    day_bounds_utc,
    now_utc,
    opening_window_utc,
    today_in_shop_tz,
)

GRID_MINUTES = 15
GUARD_MINUTES = 10  # cushion before the next appointment (doc 3.6)
MAX_WAIT_MINUTES = 240


class WalkInServiceNotFoundError(Exception):
    pass


def _ceil_to_grid_dt(moment):
    discard = timedelta(
        minutes=moment.minute % GRID_MINUTES,
        seconds=moment.second,
        microseconds=moment.microsecond,
    )
    if discard:
        return moment - discard + timedelta(minutes=GRID_MINUTES)
    return moment


def _today_turns(db: Session, branch_id: UUID) -> dict[UUID, float]:
    """Today's turn ledger, rebuilt from bookings - derived data, never stored
    truth (doc edge case 7)."""
    day_start, day_end = day_bounds_utc(today_in_shop_tz())
    rows = (
        db.query(BookingDetailModel.staff_id, ServiceModel.turn_weight)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .join(ServiceModel, BookingDetailModel.service_id == ServiceModel.id)
        .filter(
            BookingModel.branch_id == branch_id,
            BookingModel.status.in_(ACTIVE_BOOKING_STATUSES),
            BookingDetailModel.staff_id.isnot(None),
            BookingDetailModel.start_time >= day_start,
            BookingDetailModel.start_time < day_end,
        )
        .all()
    )
    turns: dict[UUID, float] = {}
    for staff_id, weight in rows:
        turns[staff_id] = turns.get(staff_id, 0.0) + float(weight)
    return turns


def _unassigned_windows(db: Session, branch_id: UUID) -> list[tuple]:
    """Windows of today's legs that still have no technician (a nightly run that
    left legs unassigned, or a sick-tech release). They sit on nobody's personal
    timeline, but the time is promised to a customer and one of today's techs
    will have to serve it - so no walk-in may be seated over it."""
    day_start, day_end = day_bounds_utc(today_in_shop_tz())
    rows = (
        db.query(BookingDetailModel, ServiceModel.buffer_after_min)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .join(ServiceModel, BookingDetailModel.service_id == ServiceModel.id)
        .filter(
            BookingModel.branch_id == branch_id,
            BookingModel.status.in_(ACTIVE_BOOKING_STATUSES),
            BookingDetailModel.staff_id.is_(None),
            BookingDetailModel.start_time < day_end,
            BookingDetailModel.end_time > day_start,
        )
        .all()
    )
    return [
        (detail.start_time, detail.end_time + timedelta(minutes=buffer_after_min))
        for detail, buffer_after_min in rows
    ]


def find_walkin_options(db: Session, branch_id: UUID, service_id: UUID) -> list[dict]:
    service = db.get(ServiceModel, service_id)
    if service is None:
        raise WalkInServiceNotFoundError()

    today = today_in_shop_tz()
    open_utc, close_utc = opening_window_utc(today)
    now = max(_ceil_to_grid_dt(now_utc()), open_utc)
    deadline = now + timedelta(minutes=MAX_WAIT_MINUTES)

    matrix = load_matrix(db)
    # Today's techs at this branch, per the roster fixed at last night's close.
    staff_list = expected_staff(db, branch_id, today)
    configured = matrix_configured_for(staff_list, matrix)
    turns = _today_turns(db, branch_id)
    unassigned = _unassigned_windows(db, branch_id)

    options = []
    for staff in staff_list:
        minutes = matrix.get(staff.id, {}).get(service_id)
        if minutes is None:
            if configured:
                continue
            minutes = service.duration_min  # matrix not filled in yet
        need = timedelta(
            minutes=ceil_to_grid(minutes) + service.buffer_after_min + GUARD_MINUTES
        )
        busy = sorted(staff_timeline_busy(db, staff.id, today) + unassigned)

        cursor = now
        while cursor < min(close_utc, deadline):
            end = cursor + need
            clash = next(
                (b for b in busy if b[0] < end and b[1] > cursor),
                None,
            )
            if clash is None:
                options.append(
                    {
                        "staff_id": staff.id,
                        "staff_name": staff.display_name,
                        "start_time": cursor,
                        "end_time": cursor + timedelta(minutes=ceil_to_grid(minutes)),
                        "wait_minutes": int((cursor - now).total_seconds() // 60),
                        "turns_today": turns.get(staff.id, 0.0),
                    }
                )
                break
            cursor = _ceil_to_grid_dt(clash[1])

    # Earliest seat first; among equal waits the tech with fewest turns (3.5).
    options.sort(key=lambda option: (option["start_time"], option["turns_today"]))
    return options[:5]
