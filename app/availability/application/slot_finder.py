"""Advance-booking slot finder on the capacity ledger (doc 3.3b).

Any-tech requests list the times where every leg of the visit still fits under
the lane caps - no technician is named or reserved; that happens at the nightly
close. A named-tech request checks that one tech's personal timeline instead,
using their real minutes from the capability matrix.
"""

from datetime import date as date_type
from datetime import timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.allocation.application.roster import bookable_at, expected_staff
from app.availability.application.capacity import (
    BookingWindow,
    CapacityLedger,
    booking_window_state,
    eligible_staff,
    is_window_free,
    matrix_configured_for,
    planning_minutes,
    rare_service_cap,
    staff_timeline_busy,
)
from app.capability.application.matrix import ceil_to_grid, is_available, load_matrix
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import last_booking_utc, opening_window_utc
from app.slot_locks.application.locks import locks_overlapping
from app.staff.infrastructure.models import StaffModel

SLOT_STEP_MINUTES = 15


class ServiceNotFoundError(Exception):
    pass


class BookingWindowClosedError(Exception):
    def __init__(self, state: str):
        self.state = state
        super().__init__(state)


def build_visit_legs(
    db: Session,
    branch_id: UUID,
    services: list[ServiceModel],
    day: date_type,
    staff_id: UUID | None,
) -> list[dict] | None:
    """The visit as sequential legs with planned durations (grid-snapped).

    Any-tech legs hold the cautious planning duration (slowest eligible tech);
    named-tech legs hold that tech's real minutes. None = not sellable that day.
    """
    matrix = load_matrix(db)
    legs = []
    offset = 0
    for service in services:
        if staff_id is not None:
            staff_cells = matrix.get(staff_id, {})
            minutes = staff_cells.get(service.id)
            if minutes is None:
                if staff_cells or matrix_configured_for(
                    expected_staff(db, branch_id, day), matrix
                ):
                    return None  # the named tech cannot do this service - never assign
                minutes = service.duration_min  # matrix not filled in yet
            minutes = ceil_to_grid(minutes)
        else:
            minutes = planning_minutes(db, branch_id, service.id, day)
            if minutes is None:
                return None  # nobody expected that day can do it - don't sell
        legs.append(
            {
                "service_id": service.id,
                "skill_group": service.skill_group,
                "offset_min": offset,
                "duration_min": minutes,
                "buffer_min": service.buffer_after_min,
            }
        )
        offset += minutes
    return legs


def place_legs(legs: list[dict], visit_start) -> list[dict]:
    placed = []
    for leg in legs:
        start = visit_start + timedelta(minutes=leg["offset_min"])
        end = start + timedelta(minutes=leg["duration_min"])
        placed.append(
            {
                "service_id": leg["service_id"],
                "skill_group": leg["skill_group"],
                "start": start,
                "end": end + timedelta(minutes=leg["buffer_min"]),
                "service_end": end,
            }
        )
    return placed


def find_available_slots(
    db: Session,
    branch_id: UUID,
    service_ids: list[UUID],
    target_date: date_type,
    staff_id: UUID | None = None,
) -> list[dict]:
    services = [db.get(ServiceModel, service_id) for service_id in service_ids]
    if any(service is None for service in services):
        raise ServiceNotFoundError()

    state = booking_window_state(target_date)
    if state != BookingWindow.OPEN:
        raise BookingWindowClosedError(state)

    legs = build_visit_legs(db, branch_id, services, target_date, staff_id)
    if legs is None:
        return []
    total_minutes = legs[-1]["offset_min"] + legs[-1]["duration_min"]

    open_utc, _close_utc = opening_window_utc(target_date)
    last_start = last_booking_utc(target_date)

    # Manual locks close published times exactly as locked - no buffer around
    # them. A branch-wide lock blocks any-tech sales; a staff lock only blocks
    # that member's personal timeline.
    day_locks = locks_overlapping(
        db, branch_id, open_utc, last_start + timedelta(minutes=total_minutes)
    )

    def _locked(placed, for_staff_id) -> bool:
        for leg in placed:
            for lock in day_locks:
                if lock.staff_id is not None and lock.staff_id != for_staff_id:
                    continue
                if lock.start_time < leg["service_end"] and lock.end_time > leg["start"]:
                    return True
        return False

    if staff_id is not None:
        staff = db.get(StaffModel, staff_id)
        # Techs belong to the chain: a named request works anywhere the tech is
        # not pinned/assigned elsewhere that day (first booking wins).
        if (
            staff is None
            or not is_available(staff, target_date)
            or not bookable_at(db, staff, branch_id, target_date)
        ):
            return []
        busy = staff_timeline_busy(db, staff_id, target_date)
        checker = lambda placed: not _locked(placed, staff_id) and all(  # noqa: E731
            is_window_free(busy, leg["start"], leg["end"]) for leg in placed
        )
    else:
        ledger = CapacityLedger(db, branch_id, target_date)
        service_caps = {
            service.id: rare_service_cap(
                len(eligible_staff(db, branch_id, service.id, target_date))
            )
            for service in services
        }
        checker = lambda placed: not _locked(placed, None) and ledger.fits(  # noqa: E731
            placed, service_caps
        )

    slots = []
    cursor = open_utc
    step = timedelta(minutes=SLOT_STEP_MINUTES)
    while cursor <= last_start:
        placed = place_legs(legs, cursor)
        if checker(placed):
            slots.append(
                {
                    "staff_id": staff_id,
                    "start_time": cursor,
                    "end_time": cursor + timedelta(minutes=total_minutes),
                }
            )
        cursor += step
    return slots
