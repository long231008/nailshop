"""The owner-supplied capability matrix - the single source of truth (v3.2, 1.1).

Two layers of duration, two purposes:
- ServiceModel.duration_min is the *menu* duration customers read.
- StaffCapabilityModel.minutes is the *real* duration the scheduler runs on.

Every part of the scheduler asks this module instead of guessing: `real_minutes`
for one cell, `load_matrix` for the whole grid the capacity check shares work
out against, and `planning_minutes` (the cautious any-tech hold).
"""

import math
from datetime import date as date_type
from uuid import UUID

from sqlalchemy.orm import Session

from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.capability.infrastructure.models import StaffCapabilityModel
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import now_utc
from app.staff.infrastructure.models import StaffModel, StaffStatus

GRID_MINUTES = 15


class CapabilityValidationError(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


def ceil_to_grid(minutes: int) -> int:
    return math.ceil(minutes / GRID_MINUTES) * GRID_MINUTES


def parse_days_off(days_off: str) -> set[int]:
    return {int(part) for part in days_off.split(",") if part.strip().isdigit()}


def is_available(staff: StaffModel, day: date_type) -> bool:
    """The single gate for "can this tech work on this day" (doc 3.8b)."""
    if staff.status != StaffStatus.ACTIVE:
        return False
    return day.weekday() not in parse_days_off(staff.days_off)


def load_matrix(db: Session) -> dict[UUID, dict[UUID, int]]:
    """capability[staff_id][service_id] = real minutes. Missing cell = cannot do."""
    matrix: dict[UUID, dict[UUID, int]] = {}
    for row in db.query(StaffCapabilityModel).all():
        matrix.setdefault(row.staff_id, {})[row.service_id] = row.minutes
    return matrix


def real_minutes(db: Session, staff_id: UUID, service_id: UUID) -> int | None:
    cell = (
        db.query(StaffCapabilityModel)
        .filter(
            StaffCapabilityModel.staff_id == staff_id,
            StaffCapabilityModel.service_id == service_id,
        )
        .first()
    )
    return cell.minutes if cell else None


def get_matrix_payload(db: Session) -> dict:
    services = db.query(ServiceModel).order_by(ServiceModel.created_at).all()
    staff_list = (
        db.query(StaffModel)
        .filter(StaffModel.status != StaffStatus.BLOCKED)
        .order_by(StaffModel.created_at)
        .all()
    )
    matrix = load_matrix(db)
    return {
        "services": services,
        "staff": staff_list,
        "capability": {
            str(staff.id): {
                str(service_id): minutes for service_id, minutes in matrix.get(staff.id, {}).items()
            }
            for staff in staff_list
        },
    }


def _validate_cell(minutes: int, service: ServiceModel) -> None:
    if minutes < 5 or minutes > 240:
        raise CapabilityValidationError(
            f"Real minutes for '{service.name}' must be between 5 and 240"
        )
    if service.duration_min and minutes > 3 * service.duration_min:
        raise CapabilityValidationError(
            f"Real minutes for '{service.name}' exceed 3x the menu duration - "
            "this looks like a typo"
        )


def save_matrix(
    db: Session, capability: dict[UUID, dict[UUID, int]]
) -> tuple[list[str], list[dict]]:
    """Replace capability rows with the owner's new numbers, one technician at a
    time: each staff key in the payload replaces that technician's whole row;
    technicians absent from the payload keep their existing cells. To clear a
    technician entirely, send them with an empty mapping. (A partial payload
    must never wipe the rest of the chain's matrix.)

    Returns (warnings, affected_bookings). Removing a (tech, service) cell that a
    future assigned booking relies on does not silently fix anything - the affected
    bookings are reported so reception can call the customers (doc 1.1 rule 4).
    """
    services = {service.id: service for service in db.query(ServiceModel).all()}
    staff_ids = {staff.id for staff in db.query(StaffModel).all()}

    for staff_id, cells in capability.items():
        if staff_id not in staff_ids:
            raise CapabilityValidationError("Unknown staff member in capability matrix")
        for service_id, minutes in cells.items():
            service = services.get(service_id)
            if service is None:
                raise CapabilityValidationError("Unknown service in capability matrix")
            _validate_cell(minutes, service)

    old_matrix = load_matrix(db)

    if capability:
        db.query(StaffCapabilityModel).filter(
            StaffCapabilityModel.staff_id.in_(capability.keys())
        ).delete(synchronize_session=False)
    for staff_id, cells in capability.items():
        for service_id, minutes in cells.items():
            db.add(StaffCapabilityModel(staff_id=staff_id, service_id=service_id, minutes=minutes))

    warnings = []
    # Coverage is judged on the merged result: payload rows plus the untouched
    # rows of technicians absent from the payload.
    merged = {sid: cells for sid, cells in old_matrix.items() if sid not in capability}
    merged.update(capability)
    covered = {sid for cells in merged.values() for sid in cells}
    for service in services.values():
        if service.id not in covered:
            warnings.append(f"No technician can do '{service.name}' - it cannot be sold")

    # on_capability_change: future bookings assigned to a (tech, service) pair whose
    # cell was just removed need a human decision, never a silent reassignment.
    # Only technicians present in the payload can lose cells.
    removed_pairs = {
        (staff_id, service_id)
        for staff_id, cells in old_matrix.items()
        if staff_id in capability
        for service_id in cells
        if capability[staff_id].get(service_id) is None
    }
    affected = []
    if removed_pairs:
        future = (
            db.query(BookingDetailModel, BookingModel)
            .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
            .filter(
                BookingDetailModel.staff_id.isnot(None),
                BookingDetailModel.start_time >= now_utc(),
                BookingModel.status.notin_([BookingStatus.CANCELLED, BookingStatus.NO_SHOW]),
            )
            .all()
        )
        for detail, booking in future:
            if (detail.staff_id, detail.service_id) in removed_pairs:
                affected.append(
                    {
                        "booking_id": booking.id,
                        "service_id": detail.service_id,
                        "staff_id": detail.staff_id,
                        "start_time": detail.start_time,
                    }
                )

    db.commit()
    return warnings, affected
