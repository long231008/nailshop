"""Who works where, per day - the staffing plan (doc 3.3b + 4.2/4.4).

Technicians belong to the chain. Two layers decide their branch for a day:

1. AUTO - Step A of the nightly allocation placed them (greedy solver below).
2. Not assigned yet (open future days) - the *expected* plan used by the
   capacity ledger: home branch if set, otherwise the homeless pool is spread
   evenly across branches. Step A trues the guesswork up the evening before.

Customer wishes for a particular tech are just preferences on booking legs
(preferred_staff_id); they never reserve a technician or a branch.
"""

from datetime import date as date_type
from uuid import UUID

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.allocation.infrastructure.assignments import AssignmentSource, StaffDayAssignmentModel
from app.bookings.infrastructure.models import (
    BookingDetailModel,
    BookingModel,
    BookingStatus,
)
from app.branches.infrastructure.models import LocationModel
from app.capability.application.matrix import is_available, load_matrix
from app.leaves.application.leaves import full_day_leave_staff
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


def assignment_for(db: Session, staff_id: UUID, day: date_type) -> StaffDayAssignmentModel | None:
    return (
        db.query(StaffDayAssignmentModel)
        .filter(
            StaffDayAssignmentModel.staff_id == staff_id,
            StaffDayAssignmentModel.day == day,
        )
        .first()
    )


def expected_staff(db: Session, branch_id: UUID, day: date_type) -> list[StaffModel]:
    """The staffing plan for one branch-day (see module doc)."""
    active = (
        db.query(StaffModel)
        .filter(StaffModel.status == StaffStatus.ACTIVE)
        .order_by(StaffModel.created_at, StaffModel.id)
        .all()
    )
    available = [staff for staff in active if is_available(staff, day)]
    assignments = {
        a.staff_id: a.branch_id
        for a in db.query(StaffDayAssignmentModel).filter(StaffDayAssignmentModel.day == day).all()
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
        demand.setdefault(branch_id, {})[group] = demand.get(branch_id, {}).get(group, 0) + minutes
    return demand


def _anchored_branches(db: Session, day: date_type) -> dict[UUID, UUID]:
    """staff -> branch for every tech who already has assigned legs on `day`.
    Ties (legs at two branches after odd manual moves) go to the busier one."""
    day_start, day_end = day_bounds_utc(day)
    rows = (
        db.query(
            BookingDetailModel.staff_id,
            BookingModel.branch_id,
            func.count(BookingDetailModel.id),
        )
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .filter(
            BookingDetailModel.staff_id.isnot(None),
            BookingModel.status.in_(ACTIVE_BOOKING_STATUSES),
            BookingDetailModel.start_time >= day_start,
            BookingDetailModel.start_time < day_end,
        )
        .group_by(BookingDetailModel.staff_id, BookingModel.branch_id)
        .all()
    )
    best: dict[UUID, tuple[int, UUID]] = {}
    for staff_id, branch_id, leg_count in rows:
        current = best.get(staff_id)
        if current is None or leg_count > current[0]:
            best[staff_id] = (leg_count, branch_id)
    return {staff_id: branch_id for staff_id, (_count, branch_id) in best.items()}


def solve_day(db: Session, day: date_type) -> list[StaffDayAssignmentModel]:
    """Step A (greedy, doc 4.4): place every available tech at a branch for the
    day. Non-floating techs stay home; floating techs go where uncovered
    demand (in groups they cover) is largest, home breaking ties.
    Idempotent: the day's previous placements are replaced - except that a
    tech whose legs are already assigned that day is anchored to that branch,
    so re-running the solver mid-day can never strand assigned work at a
    branch its technician was moved away from."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key)::bigint)"),
        {"key": f"solve_day:{day.isoformat()}"},
    )
    anchored = _anchored_branches(db, day)
    delete_query = db.query(StaffDayAssignmentModel).filter(
        StaffDayAssignmentModel.day == day,
    )
    if anchored:
        delete_query = delete_query.filter(StaffDayAssignmentModel.staff_id.notin_(anchored.keys()))
    delete_query.delete(synchronize_session=False)
    db.flush()
    kept_rows = {
        a.staff_id
        for a in db.query(StaffDayAssignmentModel).filter(StaffDayAssignmentModel.day == day).all()
    }

    branches = list(db.query(LocationModel).order_by(LocationModel.created_at, LocationModel.id))
    if not branches:
        return []
    matrix = load_matrix(db)
    services = {s.id: s for s in db.query(ServiceModel).all()}
    demand = _demand_minutes(db, day)

    # Uncovered demand per branch, eaten as techs are placed. A placed tech
    # covers their capable groups with a workday's worth of time.
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
    # A tech off for the whole day is never placed, so Step A does not hand a
    # branch demand its absent technician cannot serve. Partial leave still
    # places them - the capacity ledger and materialize handle the hours off.
    on_leave = full_day_leave_staff(db, day)
    unplaced = []
    for staff in active:
        if not is_available(staff, day) or staff.id in on_leave:
            continue
        if staff.id in anchored:
            place(staff, anchored[staff.id])
            if staff.id not in kept_rows:
                db.add(
                    StaffDayAssignmentModel(
                        staff_id=staff.id,
                        branch_id=anchored[staff.id],
                        day=day,
                        source=AssignmentSource.AUTO,
                    )
                )
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

    # The round-robin spread selling counted these floaters at: homeless techs
    # go to branches in creation order, exactly as expected_staff hands them
    # out while no day assignments exist yet.
    baseline: dict[UUID, UUID] = {}
    homeless = [staff for staff in unplaced if staff.branch_id is None]
    for index, staff in enumerate(homeless):
        baseline[staff.id] = branches[index % len(branches)].id

    moved: list[tuple[StaffModel, StaffDayAssignmentModel]] = []
    for staff in unplaced:
        groups = groups_of(staff.id)
        best = min(branches, key=lambda branch: score(branch, groups, staff))
        place(staff, best.id)
        row = StaffDayAssignmentModel(
            staff_id=staff.id, branch_id=best.id, day=day, source=AssignmentSource.AUTO
        )
        db.add(row)
        home = baseline.get(staff.id, staff.branch_id)
        if home is not None and home != best.id:
            moved.append((staff, row))

    db.flush()

    # Minutes per skill group cannot see intervals: the greedy pass above can
    # pull a floater away from a branch whose SOLD book needed them - selling
    # proved each branch-day staffable with the floaters where the baseline put
    # them, not where demand totals look biggest. So verify every branch can
    # still staff what it sold, and hand moved floaters back to the branch that
    # counted them until it can. The baseline placement itself always can.
    from app.availability.application.capacity import CapacityLedger

    def staffable(branch_id: UUID) -> bool:
        # Nightly and once per branch, so it can afford a far deeper search
        # than the per-booking check - this is the last look before the
        # schedule becomes people's tomorrow.
        return CapacityLedger(db, branch_id, day).can_be_staffed([], budget=200_000)

    broken = [branch.id for branch in branches if not staffable(branch.id)]
    while broken and moved:
        target = broken[0]
        returned = False
        for index, (staff, row) in enumerate(moved):
            home = baseline.get(staff.id, staff.branch_id)
            if home == target:
                row.branch_id = home
                db.flush()
                moved.pop(index)
                returned = True
                break
        if not returned:
            break  # nobody owed to this branch - materialize will surface it
        broken = [branch.id for branch in branches if not staffable(branch.id)]

    return db.query(StaffDayAssignmentModel).filter(StaffDayAssignmentModel.day == day).all()
