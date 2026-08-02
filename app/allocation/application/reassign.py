"""Manual reassignment - how a manager grants a customer's wish.

The allocator optimises freely and ignores preferred_staff_id; afterwards a
human looks at /allocation/status and, when the finished schedule has room,
moves a leg onto the wished technician here. The same checks the allocator
uses apply, so a manual move can never double-book anyone.
"""

from datetime import timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.allocation.application.materialize import _week_assigned_minutes
from app.allocation.application.roster import expected_staff
from app.audit_log.infrastructure.models import AuditLogModel
from app.availability.application.capacity import (
    matrix_configured_for,
    staff_timeline_busy,
)
from app.bookings.infrastructure.models import (
    BookingDetailModel,
    BookingDetailStatus,
    BookingModel,
    BookingStatus,
)
from app.capability.application.matrix import ceil_to_grid, is_available, load_matrix
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import shop_timezone
from app.slot_locks.application.locks import locks_overlapping
from app.staff.infrastructure.models import StaffModel, StaffStatus


class LegNotFoundError(Exception):
    pass


class ReassignNotAllowedError(Exception):
    """The move is impossible by rule (wrong state, wrong roster, no skill)."""


class ReassignConflictError(Exception):
    """The technician is not free for that window."""


REASSIGNABLE_BOOKING_STATUSES = (BookingStatus.PENDING, BookingStatus.APPROVED)


def reassign_leg(
    db: Session, booking_detail_id: UUID, staff_id: UUID, actor_user_id: UUID
) -> tuple[BookingDetailModel, StaffModel]:
    row = (
        db.query(BookingDetailModel, BookingModel, ServiceModel)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .join(ServiceModel, BookingDetailModel.service_id == ServiceModel.id)
        .filter(BookingDetailModel.id == booking_detail_id)
        .first()
    )
    if row is None:
        raise LegNotFoundError()
    detail, booking, service = row

    if (
        booking.status not in REASSIGNABLE_BOOKING_STATUSES
        or detail.status != BookingDetailStatus.PENDING
    ):
        raise ReassignNotAllowedError("Only upcoming, not-yet-started legs can be reassigned")

    staff = db.get(StaffModel, staff_id)
    day = detail.start_time.astimezone(shop_timezone()).date()
    if staff is None or staff.status != StaffStatus.ACTIVE or not is_available(staff, day):
        raise ReassignNotAllowedError("The staff member is not working on this day")

    if detail.staff_id == staff.id:
        return detail, staff

    # One tech, one salon per day: the target must stand at this branch.
    roster = expected_staff(db, booking.branch_id, day)
    if staff.id not in {member.id for member in roster}:
        raise ReassignNotAllowedError("The staff member is not at this branch on that day")

    matrix = load_matrix(db)
    minutes = matrix.get(staff.id, {}).get(service.id)
    if minutes is None:
        if matrix_configured_for(roster, matrix):
            raise ReassignNotAllowedError("The staff member does not offer this service")
        minutes = service.duration_min  # matrix not filled in yet

    real = ceil_to_grid(minutes)
    committed = _week_assigned_minutes(db, [staff.id], day).get(staff.id, 0)
    if committed + real > staff.max_hours_week * 60:
        raise ReassignNotAllowedError("The staff member has no hours left that week")

    real_end = detail.start_time + timedelta(minutes=real)
    hold_end = real_end + timedelta(minutes=service.buffer_after_min)

    # The customer sits in one chair: a slower tech must not run into the next
    # leg of the same visit.
    next_leg_start = (
        db.query(BookingDetailModel.start_time)
        .filter(
            BookingDetailModel.booking_id == booking.id,
            BookingDetailModel.start_time > detail.start_time,
        )
        .order_by(BookingDetailModel.start_time)
        .limit(1)
        .scalar()
    )
    if next_leg_start is not None and real_end > next_leg_start:
        raise ReassignConflictError()

    # The leg being moved sits on the OLD tech's timeline, never the target's
    # (same-tech moves returned early above), so the full timeline applies.
    busy = list(staff_timeline_busy(db, staff.id, day))
    for lock in locks_overlapping(db, booking.branch_id, detail.start_time, hold_end):
        if lock.staff_id is None or lock.staff_id == staff.id:
            busy.append((lock.start_time, lock.end_time))
    if any(b_start < hold_end and b_end > detail.start_time for b_start, b_end in busy):
        raise ReassignConflictError()

    previous_staff_id = detail.staff_id
    detail.staff_id = staff.id
    detail.end_time = real_end
    detail.duration_min = int((real_end - detail.start_time).total_seconds() // 60)
    db.add(
        AuditLogModel(
            actor_user_id=actor_user_id,
            action="allocation.reassigned",
            entity_type="booking",
            entity_id=booking.id,
            details={
                "booking_detail_id": str(detail.id),
                "from_staff_id": str(previous_staff_id) if previous_staff_id else None,
                "to_staff_id": str(staff.id),
            },
        )
    )
    db.commit()
    db.refresh(detail)
    return detail, staff
