"""Step B - materialize: name a technician for every any-tech booking (doc 4.3).

Runs after the day's booking window has closed, so demand is complete and
immutable. Start times were promised to customers and stay fixed; this is pure
interval assignment. Each leg shrinks from its cautious planning duration to the
assigned tech's real minutes, so timelines reflect when techs actually finish.

Fairness follows the salon turn system (doc 3.5): every assignment adds the
service's turn weight to the tech's ledger, and the next leg goes to whoever
holds the fewest turns - except that a customer's preferred technician
(preferred_staff_id, a wish recorded at booking time) is seated first whenever
their timeline allows.
"""

from datetime import date as date_type
from datetime import timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.allocation.application.roster import expected_staff
from app.allocation.infrastructure.models import AllocationRunModel
from app.availability.application.capacity import (
    ACTIVE_BOOKING_STATUSES,
    matrix_configured_for,
)
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.capability.application.matrix import ceil_to_grid, load_matrix
from app.services.infrastructure.models import ServiceModel
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
        .order_by(BookingDetailModel.start_time)
        .all()
    )


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

    # The techs standing at this branch that day, per the roster (pins + Step A).
    staff_list = expected_staff(db, branch_id, target_date)
    matrix = load_matrix(db)
    configured = matrix_configured_for(staff_list, matrix)
    rows = _day_details(db, branch_id, target_date)

    # Personal timelines and the day's turn ledger start from what is already
    # pinned (named-tech bookings joined the timeline at booking time, doc 3.3b).
    timelines: dict[UUID, list] = {staff.id: [] for staff in staff_list}
    turns: dict[UUID, float] = {staff.id: 0.0 for staff in staff_list}
    last_finish: dict[UUID, object] = {}
    for detail, _booking, service in rows:
        if detail.staff_id in timelines:
            hold_end = detail.end_time + timedelta(minutes=service.buffer_after_min)
            timelines[detail.staff_id].append((detail.start_time, hold_end))
            turns[detail.staff_id] += float(service.turn_weight)
            prev = last_finish.get(detail.staff_id)
            if prev is None or detail.end_time > prev:
                last_finish[detail.staff_id] = detail.end_time

    # Manual slot locks close parts of the day: a staff lock blocks that tech,
    # a branch-wide lock blocks everyone. Without this, the allocator could
    # seat a customer inside a range the salon explicitly closed.
    lock_day_start, lock_day_end = day_bounds_utc(target_date)
    for lock in locks_overlapping(db, branch_id, lock_day_start, lock_day_end):
        if lock.staff_id is not None:
            if lock.staff_id in timelines:
                timelines[lock.staff_id].append((lock.start_time, lock.end_time))
        else:
            for windows in timelines.values():
                windows.append((lock.start_time, lock.end_time))

    for windows in timelines.values():
        windows.sort()

    day_start, _ = day_bounds_utc(target_date)
    assigned = 0
    unassigned = 0

    for detail, booking, service in rows:
        if detail.staff_id is not None:
            continue

        booking_staff = {
            d.staff_id
            for d, b, _s in rows
            if b.id == booking.id and d.staff_id is not None
        }
        candidates = []
        for staff in staff_list:
            minutes = matrix.get(staff.id, {}).get(service.id)
            if minutes is None:
                if configured:
                    continue
                minutes = service.duration_min  # matrix not filled in yet
            real_end = detail.start_time + timedelta(minutes=ceil_to_grid(minutes))
            hold_end = real_end + timedelta(minutes=service.buffer_after_min)
            if not all(
                b_end <= detail.start_time or b_start >= hold_end
                for b_start, b_end in timelines[staff.id]
            ):
                continue
            candidates.append((staff, real_end, hold_end))

        if not candidates:
            unassigned += 1
            continue

        affinity = _customer_affinity(
            db, booking.customer_id, [staff.id for staff, _, _ in candidates]
        )
        staff, real_end, hold_end = min(
            candidates,
            key=lambda entry: (
                entry[0].id != detail.preferred_staff_id,  # the customer's wish first
                turns[entry[0].id],  # then fairness (doc 3.5)
                entry[0].id not in booking_staff,  # continuity within the visit
                entry[0].id not in affinity,  # the customer's usual tech
                last_finish.get(entry[0].id, day_start),  # longest idle wins ties
            ),
        )

        detail.staff_id = staff.id
        # Shrink to the assigned tech's real minutes (v3.2) so the timeline
        # shows when the tech is really free again.
        detail.end_time = real_end
        detail.duration_min = int((real_end - detail.start_time).total_seconds() // 60)
        timelines[staff.id].append((detail.start_time, hold_end))
        timelines[staff.id].sort()
        turns[staff.id] += float(service.turn_weight)
        prev = last_finish.get(staff.id)
        if prev is None or real_end > prev:
            last_finish[staff.id] = real_end
        assigned += 1

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
