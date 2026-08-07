"""Step B - materialize: name a technician for every any-tech booking (doc 4.3).

Runs after the day's booking window has closed, so demand is complete and
immutable. Start times were promised to customers and stay fixed; this is pure
interval assignment. Each leg shrinks from its cautious planning duration to the
assigned tech's real minutes, so timelines reflect when techs actually finish.

Fairness follows the salon turn system (doc 3.5): every assignment adds the
service's turn weight to the tech's ledger, and the next leg goes to whoever
holds the fewest turns. A customer's preferred technician (preferred_staff_id)
is deliberately NOT an input here - the allocator stays free to optimise; the
wish is surfaced on /allocation/status for a manager to grant by hand via
/allocation/reassign when the finished schedule allows it.
"""

from datetime import date as date_type
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.allocation.application.roster import expected_staff
from app.allocation.infrastructure.models import AllocationRunModel
from app.availability.application.capacity import (
    ACTIVE_BOOKING_STATUSES,
    _slots_covered,
    branch_resource_caps,
    matrix_configured_for,
)
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.branches.infrastructure.models import LocationModel
from app.capability.application.matrix import ceil_to_grid, load_matrix, working_window
from app.leaves.application.leaves import leaves_for_day
from app.services.infrastructure.models import ServiceExtensionModel, ServiceModel
from app.shared.infrastructure.clock import day_bounds_utc
from app.slot_locks.application.locks import locks_overlapping


def _day_details(db: Session, branch_id: UUID, target_date: date_type):
    day_start, day_end = day_bounds_utc(target_date)
    return (
        db.query(BookingDetailModel, BookingModel, ServiceModel)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .join(ServiceModel, BookingDetailModel.service_id == ServiceModel.id)
        .filter(
            BookingModel.branch_id == branch_id,
            BookingModel.status.in_(ACTIVE_BOOKING_STATUSES),
            BookingDetailModel.start_time >= day_start,
            BookingDetailModel.start_time < day_end,
        )
        # This order IS the order technicians get handed out: among legs that
        # start together the first one seen picks first, while the turn ledger
        # still has them level. start_time alone leaves those ties to Postgres,
        # which reshuffles them whenever a row is rewritten - an admin approving
        # a booking is enough - so the same day could allocate two different
        # ways. Booked-first picks first: settled, and explainable to a customer.
        .order_by(
            BookingDetailModel.start_time,
            BookingModel.created_at,
            BookingDetailModel.id,
        )
        .all()
    )


def _length_minutes(db: Session, details) -> dict[UUID, int]:
    """detail_id -> extra minutes its chosen length adds.

    A long set is longer for whoever does it, so the minutes belong to the leg
    on top of the technician's own time for the service. Without this the
    nightly run would recompute the leg from the capability cell alone and
    silently shrink a long set back to a short one.
    """
    extension_ids = {d.service_extension_id for d in details if d.service_extension_id}
    if not extension_ids:
        return {}
    extra = dict(
        db.query(ServiceExtensionModel.id, ServiceExtensionModel.extra_duration_min)
        .filter(ServiceExtensionModel.id.in_(extension_ids))
        .all()
    )
    return {d.id: extra.get(d.service_extension_id, 0) for d in details if d.service_extension_id}


def _week_assigned_minutes(db: Session, staff_ids: list[UUID], day: date_type) -> dict[UUID, int]:
    """Minutes already on each tech's plate for the ISO week of `day` - the
    ledger behind the max_hours_week guard."""
    if not staff_ids:
        return {}
    week_start = day - timedelta(days=day.weekday())
    week_end = week_start + timedelta(days=7)
    rows = (
        db.query(
            BookingDetailModel.staff_id,
            func.coalesce(func.sum(BookingDetailModel.duration_min), 0),
        )
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .filter(
            BookingDetailModel.staff_id.in_(staff_ids),
            BookingModel.status.in_(ACTIVE_BOOKING_STATUSES),
            BookingModel.booking_date >= week_start,
            BookingModel.booking_date < week_end,
        )
        .group_by(BookingDetailModel.staff_id)
        .all()
    )
    return {staff_id: int(minutes) for staff_id, minutes in rows}


def _customer_affinity(db: Session, customer_id: UUID, staff_ids: list[UUID]) -> set[UUID]:
    """Techs who have completed work for this customer before - the "any tech"
    that quietly remembers the customer's usual tech (doc 3.4)."""
    if not staff_ids:
        return set()
    rows = (
        db.query(BookingDetailModel.staff_id)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .filter(
            BookingModel.customer_id == customer_id,
            BookingModel.status == BookingStatus.COMPLETED,
            BookingDetailModel.staff_id.in_(staff_ids),
        )
        .distinct()
        .all()
    )
    return {row[0] for row in rows}


def materialize_day(db: Session, branch_id: UUID, target_date: date_type) -> AllocationRunModel:
    """Assign a tech to every unassigned leg of one branch-day. Idempotent: an
    advisory lock serialises runs and already-assigned legs are never touched."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key)::bigint)"),
        {"key": f"allocation:{branch_id}:{target_date.isoformat()}"},
    )

    # The techs standing at this branch that day, per the roster (Step A).
    staff_list = expected_staff(db, branch_id, target_date)
    matrix = load_matrix(db)
    configured = matrix_configured_for(staff_list, matrix)
    rows = _day_details(db, branch_id, target_date)
    length_minutes = _length_minutes(db, [detail for detail, _b, _s in rows])

    # The owner's weekly ceiling per tech (fix #4, restored): assigning past it
    # is refused here, exactly where assignment happens now.
    week_minutes = _week_assigned_minutes(db, [s.id for s in staff_list], target_date)
    week_limit = {s.id: s.max_hours_week * 60 for s in staff_list}

    # Chairs, tables and beds. Selling counts them on the widest possible span,
    # so a fresh day always fits - but this run SHRINKS legs to the assigned
    # tech's minutes, walk-ins are then sold into the space freed, and a sick
    # call can hand a shrunk leg to a slower tech again. Without a count here
    # that puts two customers on one chair with nobody noticing. Occupancy
    # follows the same rule as selling: the leg's span plus its buffer.
    branch = db.get(LocationModel, branch_id)
    resource_caps = branch_resource_caps(branch) if branch is not None else {}
    resource_used: dict[tuple, int] = {}

    def occupy(resource, start, hold_end, delta):
        if resource not in resource_caps:
            return
        for slot in _slots_covered(start, hold_end):
            key = (resource, slot)
            resource_used[key] = resource_used.get(key, 0) + delta

    def resource_free(service, start, hold_end):
        if service.resource not in resource_caps:
            return True
        return all(
            resource_used.get((service.resource, slot), 0) < resource_caps[service.resource]
            for slot in _slots_covered(start, hold_end)
        )

    # Personal timelines and the day's turn ledger start from legs that already
    # have a technician: an earlier run of this job, or a manual reassignment.
    # Those are settled, so they live in `fixed` and the repair pass below never
    # touches them. Legs this run seats itself go in `seated`, where they can
    # still be moved to make room for someone who would otherwise get nobody.
    fixed: dict[UUID, list] = {staff.id: [] for staff in staff_list}
    seated: dict[UUID, list] = {staff.id: [] for staff in staff_list}
    turns: dict[UUID, float] = {staff.id: 0.0 for staff in staff_list}
    last_finish: dict[UUID, object] = {}
    for detail, _booking, service in rows:
        if detail.staff_id is not None:
            occupy(
                service.resource,
                detail.start_time,
                detail.end_time + timedelta(minutes=service.buffer_after_min),
                +1,
            )
        if detail.staff_id in fixed:
            hold_end = detail.end_time + timedelta(minutes=service.buffer_after_min)
            fixed[detail.staff_id].append((detail.start_time, hold_end))
            turns[detail.staff_id] += float(service.turn_weight)
            prev = last_finish.get(detail.staff_id)
            if prev is None or detail.end_time > prev:
                last_finish[detail.staff_id] = detail.end_time

    # Manual slot locks close parts of the day: a staff lock blocks that tech,
    # a branch-wide lock blocks everyone. Without this, the allocator could
    # seat a customer inside a range the salon explicitly closed.
    lock_day_start, lock_day_end = day_bounds_utc(target_date)

    # A part-timer is only in for part of the day. Blocking off the rest as if
    # it were leave means every check downstream - the greedy pass, the repair
    # walk, the week ledger - honours their hours without knowing about them.
    for staff in staff_list:
        work_start, work_end = working_window(staff, target_date)
        fixed[staff.id].append((lock_day_start, work_start))
        fixed[staff.id].append((work_end, lock_day_end))

    for lock in locks_overlapping(db, branch_id, lock_day_start, lock_day_end):
        if lock.staff_id is not None:
            if lock.staff_id in fixed:
                fixed[lock.staff_id].append((lock.start_time, lock.end_time))
        else:
            for windows in fixed.values():
                windows.append((lock.start_time, lock.end_time))

    # Staff leave follows the tech across the chain: fold each leave window into
    # their personal timeline so no leg is ever seated during it (edge case 10's
    # sibling - a planned absence rather than a same-day sick call).
    leave_windows = leaves_for_day(db, [staff.id for staff in staff_list], target_date)
    for staff_id, windows in leave_windows.items():
        if staff_id in fixed:
            fixed[staff_id].extend(windows)

    for windows in fixed.values():
        windows.sort()

    day_start, _ = day_bounds_utc(target_date)
    by_id = {detail.id: (detail, service) for detail, _booking, service in rows}
    pending = [detail.id for detail, _booking, _service in rows if detail.staff_id is None]

    def shape(detail, service, staff_id):
        """(minutes, end, hold_end) for this tech doing this leg, or None if the
        matrix says they cannot do it at all."""
        minutes = matrix.get(staff_id, {}).get(service.id)
        if minutes is None:
            if configured:
                return None
            minutes = service.duration_min  # matrix not filled in yet
        real = ceil_to_grid(minutes + length_minutes.get(detail.id, 0))
        end = detail.start_time + timedelta(minutes=real)
        return real, end, end + timedelta(minutes=service.buffer_after_min)

    def blockers(staff_id, start, hold_end, ignore=()):
        """What stands between this tech and that window: None if something that
        can never give way (a lock, leave, or a settled leg), otherwise the legs
        this run seated there - which the repair pass may move."""
        if not all(b <= start or a >= hold_end for a, b in fixed[staff_id]):
            return None
        return [
            detail_id
            for detail_id, (a, b) in seated[staff_id]
            if detail_id not in ignore and a < hold_end and b > start
        ]

    def place(detail, service, staff_id, real, end, hold_end):
        detail.staff_id = staff_id
        # Shrink to the assigned tech's real minutes (v3.2) so the timeline
        # shows when the tech is really free again.
        detail.end_time = end
        detail.duration_min = real
        seated[staff_id].append((detail.id, (detail.start_time, hold_end)))
        occupy(service.resource, detail.start_time, hold_end, +1)
        week_minutes[staff_id] = week_minutes.get(staff_id, 0) + real
        turns[staff_id] += float(service.turn_weight)

    def lift(detail, service):
        """Take a leg back off a tech, undoing everything place() recorded."""
        staff_id = detail.staff_id
        for entry_id, (span_start, span_end) in seated[staff_id]:
            if entry_id == detail.id:
                occupy(service.resource, span_start, span_end, -1)
                break
        seated[staff_id] = [entry for entry in seated[staff_id] if entry[0] != detail.id]
        week_minutes[staff_id] -= detail.duration_min
        turns[staff_id] -= float(service.turn_weight)
        detail.staff_id = None

    def snapshot():
        """Everything the repair pass below can change, so a branch that leads
        nowhere can be abandoned wholesale instead of unpicked step by step."""
        return (
            {
                detail_id: (detail.staff_id, detail.end_time, detail.duration_min)
                for detail_id, (detail, _service) in by_id.items()
            },
            {staff_id: list(legs) for staff_id, legs in seated.items()},
            dict(turns),
            dict(week_minutes),
            dict(resource_used),
        )

    def restore(state):
        legs, seats, turn_ledger, week_load, resources = state
        for detail_id, (staff_id, end, minutes) in legs.items():
            detail = by_id[detail_id][0]
            detail.staff_id, detail.end_time, detail.duration_min = staff_id, end, minutes
        seated.update({staff_id: list(entries) for staff_id, entries in seats.items()})
        turns.update(turn_ledger)
        week_minutes.clear()
        week_minutes.update(week_load)
        resource_used.clear()
        resource_used.update(resources)

    for detail, booking, service in rows:
        if detail.staff_id is not None:
            continue

        booking_staff = {
            d.staff_id for d, b, _s in rows if b.id == booking.id and d.staff_id is not None
        }
        candidates = []
        for staff in staff_list:
            fit = shape(detail, service, staff.id)
            if fit is None:
                continue
            real, end, hold_end = fit
            if week_minutes.get(staff.id, 0) + real > week_limit[staff.id]:
                continue  # no hours left that week
            if not resource_free(service, detail.start_time, hold_end):
                continue  # every chair/table/bed is taken for that span
            busy = blockers(staff.id, detail.start_time, hold_end)
            if busy is None or busy:
                continue
            candidates.append((staff, real, end, hold_end))

        if not candidates:
            continue  # the repair pass below gets a second go at this one

        affinity = _customer_affinity(db, booking.customer_id, [e[0].id for e in candidates])
        staff, real, end, hold_end = min(
            candidates,
            key=lambda entry: (
                turns[entry[0].id],  # fairness first (doc 3.5)
                entry[0].id not in booking_staff,  # continuity within the visit
                entry[0].id not in affinity,  # the customer's usual tech
                last_finish.get(entry[0].id, day_start),  # longest idle wins ties
            ),
        )
        place(detail, service, staff.id, real, end, hold_end)
        prev = last_finish.get(staff.id)
        if prev is None or end > prev:
            last_finish[staff.id] = end

    # Repair. The pass above is greedy and takes fairness first, so it happily
    # gives a job several techs could do to the one tech a later job needs, and
    # that later job then has nobody - even though a complete assignment did
    # exist. Capacity only sold the day because an exact matching existed
    # (availability/capacity.py), so a leg left over here is this allocator
    # failing to find one, not an oversell.
    #
    # So walk an augmenting path: offer the leg to a tech who is busy, and move
    # the leg standing in the way to someone else, recursively. Fairness still
    # decides everything the greedy pass could settle - this only runs where a
    # customer would otherwise be left with no technician at all.
    #
    # Three things keep the walk honest. `tried` holds the (leg, tech) pairs on
    # the chain being explored right now - pairs, not techs, because a tech
    # booked at 14:00 is still free at 09:00, and only the current chain,
    # because a pairing that fails against one arrangement is often exactly
    # right against the next. `reserved` is the window being cleared for the leg
    # one level up, so the leg moving out of the way cannot quietly move back
    # into it. `steps` is the search budget: this is a search, and a busy
    # branch-day is a big one.
    def reseat(detail_id, tried: set, reserved: list, steps: list) -> bool:
        detail, service = by_id[detail_id]
        for staff in staff_list:
            if (detail_id, staff.id) in tried or steps[0] <= 0:
                continue
            steps[0] -= 1
            fit = shape(detail, service, staff.id)
            if fit is None:
                continue
            real, end, hold_end = fit
            if week_minutes.get(staff.id, 0) + real > week_limit[staff.id]:
                continue
            if any(
                held == staff.id and start < hold_end and until > detail.start_time
                for held, start, until in reserved
            ):
                continue
            if not resource_free(service, detail.start_time, hold_end):
                continue  # moving techs never frees a chair the day has not got
            busy = blockers(staff.id, detail.start_time, hold_end)
            if busy is None:
                continue  # a lock or leave, which never gives way
            marks = set(tried)
            tried.add((detail_id, staff.id))

            undo = snapshot()
            held = reserved + [(staff.id, detail.start_time, hold_end)]
            if all(move(victim_id, tried, held, steps) for victim_id in busy):
                place(detail, service, staff.id, real, end, hold_end)
                return True
            # Abandon the branch whole: the legs go back where they were, and so
            # do the marks. Rewinding only this level's mark would leave the ones
            # its successful sub-chains added, quietly barring a tech that the
            # next branch needs.
            restore(undo)
            tried.clear()
            tried.update(marks)
        return False

    def move(victim_id, tried, held, steps) -> bool:
        victim, victim_service = by_id[victim_id]
        lift(victim, victim_service)
        return reseat(victim_id, tried, held, steps)

    for detail_id in pending:
        if by_id[detail_id][0].staff_id is None:
            # A budget, because the walk is a search and a busy branch-day is a
            # big one. Running out just means this leg waits for a human.
            reseat(detail_id, set(), [], [5000])

    assigned = sum(1 for detail_id in pending if by_id[detail_id][0].staff_id is not None)
    unassigned = len(pending) - assigned

    run = AllocationRunModel(
        branch_id=branch_id,
        target_date=target_date,
        assigned_count=assigned,
        unassigned_count=unassigned,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def release_staff_assignments(
    db: Session, staff_id: UUID, branch_id: UUID, target_date: date_type
) -> int:
    """A tech calls in sick after the close (edge case 10): free all their legs
    so a re-run can hand them to someone else. Named techs are preferences,
    not promises, so nothing needs a phone call first."""
    day_start, day_end = day_bounds_utc(target_date)
    details = (
        db.query(BookingDetailModel)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .filter(
            BookingModel.branch_id == branch_id,
            BookingModel.status.in_([BookingStatus.PENDING, BookingStatus.APPROVED]),
            BookingDetailModel.staff_id == staff_id,
            BookingDetailModel.start_time >= day_start,
            BookingDetailModel.start_time < day_end,
        )
        .all()
    )
    for detail in details:
        detail.staff_id = None
    db.commit()
    return len(details)
