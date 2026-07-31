"""The owner-supplied capability matrix - the single source of truth (v3.2, 1.1).

Two layers of duration, two purposes:
- ServiceModel.duration_min is the *menu* duration customers read.
- StaffCapabilityModel.minutes is the *real* duration the scheduler runs on.

Every part of the scheduler asks this module instead of guessing:
`can_do`, `real_minutes`, `planning_minutes` (the cautious any-tech hold),
and `eligible_staff` (who could do a service on a given day).
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


def branch_matrix_configured(db: Session, branch_id: UUID) -> bool:
    """Has the owner filled in any capability cell for this branch's techs?

    Until they do, the scheduler runs in legacy mode: menu durations, every
    active tech assumed able to do every service. The moment the first cell is
    saved, the matrix becomes the single source of truth (v3.2)."""
    return (
        db.query(StaffCapabilityModel.id)
        .join(StaffModel, StaffCapabilityModel.staff_id == StaffModel.id)
        .filter(StaffModel.branch_id == branch_id)
        .first()
        is not None
    )


def eligible_staff(
    db: Session, branch_id: UUID, service_id: UUID, day: date_type
) -> list[StaffModel]:
    """Active techs of the branch expected in on `day` who can do the service."""
    if not branch_matrix_configured(db, branch_id):
        rows = (
            db.query(StaffModel)
            .filter(StaffModel.branch_id == branch_id, StaffModel.status == StaffStatus.ACTIVE)
            .all()
        )
        return [staff for staff in rows if is_available(staff, day)]
    rows = (
        db.query(StaffModel)
        .join(StaffCapabilityModel, StaffCapabilityModel.staff_id == StaffModel.id)
        .filter(
            StaffModel.branch_id == branch_id,
            StaffModel.status == StaffStatus.ACTIVE,
            StaffCapabilityModel.service_id == service_id,
        )
        .all()
    )
    return [staff for staff in rows if is_available(staff, day)]


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


def planning_minutes(db: Session, branch_id: UUID, service_id: UUID, day: date_type) -> int | None:
    """Cautious any-tech hold: the slowest eligible tech's real minutes, snapped
    to the grid. None when nobody expected that day can do the service - the
    service must not be sold for that day (doc 1.1 rule 3)."""
    staff_list = eligible_staff(db, branch_id, service_id, day)
    if not staff_list:
        return None
    if not branch_matrix_configured(db, branch_id):
        service = db.get(ServiceModel, service_id)
        return ceil_to_grid(service.duration_min)
    minutes = [
        m
        for staff in staff_list
        if (m := real_minutes(db, staff.id, service_id)) is not None
    ]
    return ceil_to_grid(max(minutes)) if minutes else None


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
                str(service_id): minutes
                for service_id, minutes in matrix.get(staff.id, {}).items()
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
    """Replace the capability matrix with the owner's new numbers.

    Returns (warnings, affected_bookings). Removing a (tech, service) cell that a
    future pinned booking relies on does not silently fix anything - the affected
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

    db.query(StaffCapabilityModel).delete()
    for staff_id, cells in capability.items():
        for service_id, minutes in cells.items():
            db.add(
                StaffCapabilityModel(staff_id=staff_id, service_id=service_id, minutes=minutes)
            )

    warnings = []
    covered = {sid for cells in capability.values() for sid in cells}
    for service in services.values():
        if service.id not in covered:
            warnings.append(f"No technician can do '{service.name}' - it cannot be sold")

    # on_capability_change: future bookings pinned to a (tech, service) pair whose
    # cell was just removed need a human decision, never a silent reassignment.
    removed_pairs = {
        (staff_id, service_id)
        for staff_id, cells in old_matrix.items()
        for service_id in cells
        if capability.get(staff_id, {}).get(service_id) is None
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
