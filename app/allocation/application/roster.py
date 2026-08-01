"""Who works where, per day - pins and the staffing plan (doc 3.3b + 4.2/4.4).

Technicians belong to the chain. Three layers decide their branch for a day:

1. PIN - a customer booked them by name at a branch: exclusive, first booking
   wins, enforced by the (staff, day) unique constraint plus an advisory lock.
2. AUTO - Step A of the nightly allocation placed them (greedy solver below).
3. Neither yet (open future days) - the *expected* plan used by the capacity
   ledger: home branch if set, otherwise the homeless pool is spread evenly
   across branches. CAP_FILL's cushion absorbs the guesswork; Step A trues it
   up the evening before.
"""

from datetime import date as date_type
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.allocation.infrastructure.assignments import AssignmentSource, StaffDayAssignmentModel
from app.bookings.infrastructure.models import (
    BookingDetailModel,
    BookingModel,
    BookingStatus,
)
from app.branches.infrastructure.models import LocationModel
from app.capability.application.matrix import is_available, load_matrix
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import day_bounds_utc
from app.staff.infrastructure.models import StaffModel, StaffStatus

# Kept local (not imported from capacity) so the import graph stays one-way:
# matrix <- roster <- capacity.
ACTIVE_BOOKING_STATUSES = [
    BookingStatus.PENDING,
    BookingStatus.APPROVED,
    BookingStatus.IN_PROGRESS,
    BookingStatus.COMPLETED,
]


class PinConflictError(Exception):
    """The technician is already pinned to another salon that day."""

    def __init__(self, other_branch_id: UUID):
        self.other_branch_id = other_branch_id
        super().__init__(str(other_branch_id))


def assignment_for(db: Session, staff_id: UUID, day: date_type) -> StaffDayAssignmentModel | None:
    return (
        db.query(StaffDayAssignmentModel)
        .filter(
            StaffDayAssignmentModel.staff_id == staff_id,
            StaffDayAssignmentModel.day == day,
        )
        .first()
    )


def create_pin(db: Session, staff_id: UUID, branch_id: UUID, day: date_type) -> None:
    """Claim the technician for this branch-day (first-booking-wins).

    The advisory lock serialises two salons racing for the same tech in the
    same second; the unique constraint backstops everything else.
    """
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key)::bigint)"),
        {"key": f"pin:{staff_id}:{day.isoformat()}"},
    )
    existing = assignment_for(db, staff_id, day)
    if existing is None:
        db.add(
            StaffDayAssignmentModel(
                staff_id=staff_id, branch_id=branch_id, day=day, source=AssignmentSource.PIN
            )
        )
        db.flush()
        return
    if existing.branch_id != branch_id:
        raise PinConflictError(existing.branch_id)
    if existing.source == AssignmentSource.AUTO:
        existing.source = AssignmentSource.PIN  # a named booking hardens the auto placement


def release_pin_if_unused(db: Session, staff_id: UUID, day: date_type) -> None:
    """Cancelling the LAST named booking of (tech, day) releases the pin, so
    other salons can claim the technician again (doc 3.3b pin lifecycle)."""
    assignment = assignment_for(db, staff_id, day)
    if assignment is None or assignment.source != AssignmentSource.PIN:
        return
    day_start, day_end = day_bounds_utc(day)
    still_pinned = (
        db.query(BookingDetailModel.id)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .filter(
            BookingDetailModel.staff_id == staff_id,
            BookingDetailModel.staff_requested.is_(True),
            BookingModel.status.in_(ACTIVE_BOOKING_STATUSES),
            BookingDetailModel.start_time >= day_start,
            BookingDetailModel.start_time < day_end,
        )
        .first()
    )
    if still_pinned is None:
        db.delete(assignment)


def bookable_at(db: Session, staff: StaffModel, branch_id: UUID, day: date_type) -> bool:
    """May this tech serve a NAMED booking at this branch on this day?

    Pinned/assigned elsewhere - no. Otherwise any floating tech may be claimed
    anywhere (first booking wins); a non-floating tech only at their home.
    """
    assignment = assignment_for(db, staff.id, day)
    if assignment is not None:
        return assignment.branch_id == branch_id
    if not staff.floating and staff.branch_id is not None:
        return staff.branch_id == branch_id
    return True


def expected_staff(db: Session, branch_id: UUID, day: date_type) -> list[StaffModel]:
    """The staffing plan for one branch-day, in three layers (see module doc)."""
    active = (
        db.query(StaffModel)
        .filter(StaffModel.status == StaffStatus.ACTIVE)
        .order_by(StaffModel.created_at, StaffModel.id)
        .all()
    )
    available = [staff for staff in active if is_available(staff, day)]
    assignments = {
        a.staff_id: a.branch_id
        for a in db.query(StaffDayAssignmentModel)
        .filter(StaffDayAssignmentModel.day == day)
        .all()
    }

    expected = []
    homeless_pool = []
    for staff in available:
        assigned = assignments.get(staff.id)
        if assigned is not None:
            if assigned == branch_id:
                expected.append(staff)
        elif staff.branch_id is not None:
            if staff.branch_id == branch_id:
                expected.append(staff)
        else:
            homeless_pool.append(staff)

    if homeless_pool:
        branches = [
            b.id
            for b in db.query(LocationModel).order_by(LocationModel.created_at, LocationModel.id)
        ]
        for index, staff in enumerate(homeless_pool):
            if branches and branches[index % len(branches)] == branch_id:
                expected.append(staff)

    return expected


def _demand_minutes(db: Session, day: date_type) -> dict[UUID, dict[str, int]]:
    """Booked minutes per (branch, skill group) for the day - the demand Step A
    covers. By 21:00 this is the real, frozen demand (doc 4.1)."""
    day_start, day_end = day_bounds_utc(day)
    rows = (
        db.query(BookingModel.branch_id, ServiceModel.skill_group, BookingDetailModel.duration_min)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .join(ServiceModel, BookingDetailModel.service_id == ServiceModel.id)
        .filter(
            BookingModel.status.in_(ACTIVE_BOOKING_STATUSES),
            BookingDetailModel.start_time >= day_start,
            BookingDetailModel.start_time < day_end,
        )
        .all()
    )
    demand: dict[UUID, dict[str, int]] = {}
    for branch_id, group, minutes in rows:
        demand.setdefault(branch_id, {})[group] = (
            demand.get(branch_id, {}).get(group, 0) + minutes
        )
    return demand


def solve_day(db: Session, day: date_type) -> list[StaffDayAssignmentModel]:
    """Step A (greedy, doc 4.4): place every available tech at a branch for the
    day. Pins are untouchable; non-floating techs stay home; floating techs go
    where uncovered demand (in groups they cover) is largest, home breaking
    ties. Idempotent: previous AUTO rows for the day are replaced."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key)::bigint)"),
        {"key": f"solve_day:{day.isoformat()}"},
    )
    db.query(StaffDayAssignmentModel).filter(
        StaffDayAssignmentModel.day == day,
        StaffDayAssignmentModel.source == AssignmentSource.AUTO,
    ).delete()
    db.flush()

    branches = list(db.query(LocationModel).order_by(LocationModel.created_at, LocationModel.id))
    if not branches:
        return []
    matrix = load_matrix(db)
    services = {s.id: s for s in db.query(ServiceModel).all()}
    demand = _demand_minutes(db, day)
    pins = {
        a.staff_id: a
        for a in db.query(StaffDayAssignmentModel)
        .filter(StaffDayAssignmentModel.day == day)
        .all()
    }

    # Uncovered demand per branch, eaten as techs are placed. A pinned or
    # placed tech covers their capable groups with a workday's worth of time.
    WORKDAY_MINUTES = 8 * 60
    uncovered: dict[UUID, dict[str, int]] = {
        branch.id: dict(demand.get(branch.id, {})) for branch in branches
    }
    headcount: dict[UUID, int] = {branch.id: 0 for branch in branches}

    def groups_of(staff_id: UUID) -> set[str]:
        return {
            services[service_id].skill_group
            for service_id in matrix.get(staff_id, {})
            if service_id in services
        }

    def place(staff: StaffModel, branch_id: UUID) -> None:
        headcount[branch_id] += 1
        budget = WORKDAY_MINUTES
        groups = groups_of(staff.id)
        for group in sorted(groups, key=lambda g: -uncovered[branch_id].get(g, 0)):
            if budget <= 0:
                break
            need = uncovered[branch_id].get(group, 0)
            eaten = min(need, budget)
            uncovered[branch_id][group] = need - eaten
            budget -= eaten

    active = (
        db.query(StaffModel)
        .filter(StaffModel.status == StaffStatus.ACTIVE)
        .order_by(StaffModel.created_at, StaffModel.id)
        .all()
    )
    unplaced = []
    for staff in active:
        if not is_available(staff, day):
            continue
        pin = pins.get(staff.id)
        if pin is not None:
            place(staff, pin.branch_id)
            continue
        if not staff.floating and staff.branch_id is not None:
            place(staff, staff.branch_id)
            db.add(
                StaffDayAssignmentModel(
                    staff_id=staff.id,
                    branch_id=staff.branch_id,
                    day=day,
                    source=AssignmentSource.AUTO,
                )
            )
            continue
        unplaced.append(staff)

    # Floating techs, one at a time: biggest uncovered demand they can serve;
    # tie -> home branch; then the emptiest floor. Deterministic throughout.
    def score(branch, groups, staff):
        need = sum(
            minutes
            for group, minutes in uncovered[branch.id].items()
            if not groups or group in groups
        )
        return (
            -need,
            0 if staff.branch_id == branch.id else 1,
            headcount[branch.id],
            str(branch.id),
        )

    for staff in unplaced:
        groups = groups_of(staff.id)
        best = min(branches, key=lambda branch: score(branch, groups, staff))
        place(staff, best.id)
        db.add(
            StaffDayAssignmentModel(
                staff_id=staff.id, branch_id=best.id, day=day, source=AssignmentSource.AUTO
            )
        )

    db.flush()
    return (
        db.query(StaffDayAssignmentModel).filter(StaffDayAssignmentModel.day == day).all()
    )
