"""Advance-booking slot finder on the capacity ledger (doc 3.3b).

Lists the times where every leg of the visit still fits under the lane caps -
no technician is named or reserved; that happens at the nightly close. A
preferred technician does not change which times are sellable: the wish is
recorded on the booking for a manager to grant by hand afterwards.
"""

from datetime import date as date_type
from datetime import timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.availability.application.capacity import (
    BookingWindow,
    CapacityLedger,
    booking_window_state,
    existing_legs,
    planning_minutes,
)
from app.capability.application.matrix import ceil_to_grid
from app.services.infrastructure.models import ServiceExtensionModel, ServiceModel
from app.shared.infrastructure.clock import last_booking_utc, opening_window_utc
from app.slot_locks.application.locks import locks_overlapping

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
    extra_minutes: list[int] | None = None,
) -> list[dict] | None:
    """The visit as sequential legs with planned durations (grid-snapped).

    Every leg holds the cautious planning duration (slowest eligible tech), plus
    the minutes of the length chosen for it, so the times offered are the times
    that visit can really be booked at. None = not sellable that day.
    """
    legs = []
    offset = 0
    for index, service in enumerate(services):
        minutes = planning_minutes(db, branch_id, service.id, day)
        if minutes is None:
            return None  # nobody expected that day can do it - don't sell
        extra = extra_minutes[index] if extra_minutes else 0
        if extra:
            minutes = ceil_to_grid(minutes + extra)
        legs.append(
            {
                "service_id": service.id,
                "skill_group": service.skill_group,
                "resource": service.resource,
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
                "resource": leg.get("resource"),
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
    extension_ids: list[UUID | None] | None = None,
) -> list[dict]:
    services = [db.get(ServiceModel, service_id) for service_id in service_ids]
    if any(service is None for service in services):
        raise ServiceNotFoundError()

    state = booking_window_state(target_date)
    if state != BookingWindow.OPEN:
        raise BookingWindowClosedError(state)

    # A length chosen for a service makes its leg longer, so the search has to
    # know about it or it would offer times the booking then cannot take.
    extra_minutes = None
    if extension_ids and any(extension_ids):
        chosen = {
            extension.id: extension
            for extension in db.query(ServiceExtensionModel)
            .filter(ServiceExtensionModel.id.in_([e for e in extension_ids if e]))
            .all()
        }
        extra_minutes = []
        for index, extension_id in enumerate(extension_ids):
            extension = chosen.get(extension_id) if extension_id else None
            if extension is not None and extension.service_id != services[index].id:
                raise ServiceNotFoundError()
            extra_minutes.append(extension.extra_duration_min if extension else 0)

    legs = build_visit_legs(db, branch_id, services, target_date, extra_minutes)
    if legs is None:
        return []
    total_minutes = legs[-1]["offset_min"] + legs[-1]["duration_min"]

    open_utc, _close_utc = opening_window_utc(target_date)
    last_start = last_booking_utc(target_date)

    # Manual locks close published times exactly as locked - no buffer around
    # them. Only branch-wide locks affect sales; staff-specific locks are
    # honoured later, by the allocator's timelines.
    day_locks = [
        lock
        for lock in locks_overlapping(
            db, branch_id, open_utc, last_start + timedelta(minutes=total_minutes)
        )
        if lock.staff_id is None
    ]

    def _locked(placed) -> bool:
        for leg in placed:
            for lock in day_locks:
                if lock.start_time < leg["service_end"] and lock.end_time > leg["start"]:
                    return True
        return False

    ledger = CapacityLedger(db, branch_id, target_date)

    def checker(placed) -> bool:
        return not _locked(placed) and ledger.fits(placed)

    # Times that keep the salon's day tight, used only to flag a slot as
    # recommended - every sellable time is still offered and bookable.
    booked = existing_legs(db, branch_id, target_date)
    tidy_starts = {open_utc} | {leg["end"] for leg in booked}
    tidy_ends = {leg["start"] for leg in booked}

    slots = []
    cursor = open_utc
    step = timedelta(minutes=SLOT_STEP_MINUTES)
    while cursor <= last_start:
        placed = place_legs(legs, cursor)
        if checker(placed):
            visit_end = cursor + timedelta(minutes=total_minutes)
            slots.append(
                {
                    "staff_id": None,
                    "start_time": cursor,
                    "end_time": visit_end,
                    # A hint, never a restriction: this visit either opens the
                    # day, starts the moment an earlier one frees up, or closes
                    # the gap before the next - so it leaves no dead time.
                    "recommended": cursor in tidy_starts or visit_end in tidy_ends,
                }
            )
        cursor += step
    return slots
