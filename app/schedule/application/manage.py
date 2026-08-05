"""Desk-side schedule management: staff and admins add, move and cancel the
appointments customers cannot touch themselves.

Online booking (bookings/application/create.py) stays the customer's door -
window-gated, deposit-driven, technician chosen at the nightly close. This is
the salon's own book: a walk-in or phone booking goes straight onto the grid
(status APPROVED, staff_created), optionally with the technician already named,
and the same capacity, resource, leave and timeline checks the allocator uses
still apply so the desk can never build an impossible day.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.audit_log.infrastructure.models import AuditLogModel
from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.availability.application.capacity import (
    CapacityLedger,
    planning_minutes,
    staff_timeline_busy,
)
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.branches.infrastructure.models import LocationModel
from app.capability.application.matrix import (
    ceil_to_grid,
    is_available,
    load_matrix,
    working_window,
)
from app.leaves.application.leaves import leaves_for_day
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import (
    day_bounds_utc,
    last_booking_utc,
    opening_window_utc,
    shop_timezone,
)
from app.slot_locks.application.locks import locks_overlapping
from app.staff.infrastructure.models import StaffModel, StaffStatus

RESCHEDULABLE_STATUSES = (BookingStatus.PENDING, BookingStatus.APPROVED)


class AppointmentError(Exception):
    """The appointment cannot be created or moved by rule."""


class AppointmentConflictError(Exception):
    """The chosen technician is not free for that window."""


class AppointmentNotFoundError(Exception):
    pass


def _resolve_customer(
    db: Session,
    customer_id: UUID | None,
    customer_phone: str | None,
    customer_name: str | None,
) -> UserModel:
    if customer_id is not None:
        user = db.get(UserModel, customer_id)
        if user is None:
            raise AppointmentError("Customer not found")
        return user
    if not customer_phone:
        raise AppointmentError("Provide a customer, or a phone number to create one")
    user = db.query(UserModel).filter(UserModel.phone_number == customer_phone).first()
    if user is None:
        first, _, surname = (customer_name or "").partition(" ")
        user = UserModel(
            phone_number=customer_phone,
            first_name=first or None,
            surname=surname or None,
            status=UserStatus.ACTIVE,
            role=UserRole.CUSTOMER,
        )
        db.add(user)
        db.flush()
    return user


def _leg_minutes(
    branch_id: UUID,
    db: Session,
    service: ServiceModel,
    day,
    staff: StaffModel | None,
    matrix: dict,
) -> int | None:
    """Grid-snapped minutes for one leg. A named technician runs on their own
    real minutes; an unnamed leg holds the cautious planning duration so the
    nightly allocator can still name anyone eligible."""
    if staff is not None:
        cell = matrix.get(staff.id, {}).get(service.id)
        if cell is not None:
            return ceil_to_grid(cell)
        if matrix.get(staff.id):
            return None  # the tech has a matrix but not this service
        return ceil_to_grid(service.duration_min)  # legacy: no matrix filled in yet
    return planning_minutes(db, branch_id, service.id, day)


def _staff_busy_windows(
    db: Session, staff_id: UUID, branch_id: UUID, day, exclude: set[tuple] | None = None
) -> list[tuple]:
    """Everything that keeps a tech from a customer that day: their booked legs
    (optionally minus a set being moved), slot locks that reach them, and leave."""
    exclude = exclude or set()
    windows = [w for w in staff_timeline_busy(db, staff_id, day) if w not in exclude]
    day_start, day_end = day_bounds_utc(day)
    for lock in locks_overlapping(db, branch_id, day_start, day_end):
        if lock.staff_id is None or lock.staff_id == staff_id:
            windows.append((lock.start_time, lock.end_time))
    windows.extend(leaves_for_day(db, [staff_id], day).get(staff_id, []))
    return windows


def add_appointment(
    db: Session,
    actor_user_id: UUID,
    branch_id: UUID,
    service_ids: list[UUID],
    start_time: datetime,
    staff_id: UUID | None,
    customer_id: UUID | None,
    customer_phone: str | None,
    customer_name: str | None,
) -> BookingModel:
    if not service_ids:
        raise AppointmentError("At least one service is required")
    if db.get(LocationModel, branch_id) is None:
        raise AppointmentError("Branch not found")

    start_time = start_time.astimezone(timezone.utc)
    day = start_time.astimezone(shop_timezone()).date()
    customer = _resolve_customer(db, customer_id, customer_phone, customer_name)
    matrix = load_matrix(db)

    staff = None
    if staff_id is not None:
        staff = db.get(StaffModel, staff_id)
        if staff is None or staff.status != StaffStatus.ACTIVE or not is_available(staff, day):
            raise AppointmentError("The technician is not working on this day")

    prepared: list[dict] = []
    capacity_legs: list[dict] = []
    total_price = Decimal("0")
    cursor = start_time
    for service_id in service_ids:
        service = db.get(ServiceModel, service_id)
        if service is None:
            raise AppointmentError("Service not found")
        if service.branch_id is not None and service.branch_id != branch_id:
            raise AppointmentError(f"'{service.name}' is not offered at this branch")

        minutes = _leg_minutes(branch_id, db, service, day, staff, matrix)
        if minutes is None:
            if staff is not None:
                raise AppointmentError(f"{staff.display_name} does not offer '{service.name}'")
            raise AppointmentError(f"'{service.name}' cannot be scheduled on this day")

        end_time = cursor + timedelta(minutes=minutes)
        capacity_legs.append(
            {
                "service_id": service.id,
                "skill_group": service.skill_group,
                "resource": service.resource,
                "start": cursor,
                "end": end_time + timedelta(minutes=service.buffer_after_min),
            }
        )
        prepared.append(
            {
                "service_id": service.id,
                "staff_id": staff.id if staff is not None else None,
                "start_time": cursor,
                "end_time": end_time,
                "duration_min": minutes,
                "price": Decimal(str(service.base_price)),
            }
        )
        total_price += Decimal(str(service.base_price))
        cursor = end_time

    visit_start, visit_end = capacity_legs[0]["start"], capacity_legs[-1]["end"]
    ledger = CapacityLedger(db, branch_id, day)
    if staff is not None:
        # A named tech must be free for the whole visit; their own timeline is
        # the check, so only the physical resources still cap the day.
        work_start, work_end = working_window(staff, day)
        if visit_start < work_start or visit_end > work_end:
            raise AppointmentError(f"{staff.display_name} is not working at that time")
        busy = _staff_busy_windows(db, staff.id, branch_id, day)
        if any(b_start < visit_end and b_end > visit_start for b_start, b_end in busy):
            raise AppointmentConflictError()
        if not ledger.resource_fits(capacity_legs):
            raise AppointmentError("No free chair, table or bed for this time")
    else:
        # No tech named: the day must still be staffable with this visit added,
        # exactly as an online booking would be.
        if not ledger.fits(capacity_legs):
            raise AppointmentError("This time is full at this branch")

    booking = BookingModel(
        customer_id=customer.id,
        branch_id=branch_id,
        booking_date=day,
        status=BookingStatus.APPROVED,
        total_price=total_price,
        approved_at=datetime.now(timezone.utc),
        staff_created=True,
    )
    db.add(booking)
    db.flush()
    for item in prepared:
        db.add(BookingDetailModel(booking_id=booking.id, **item))
    db.add(
        AuditLogModel(
            actor_user_id=actor_user_id,
            action="schedule.appointment_added",
            entity_type="booking",
            entity_id=booking.id,
            details={"staff_id": str(staff_id) if staff_id else None},
        )
    )
    db.commit()
    db.refresh(booking)
    return booking


def reschedule_appointment(
    db: Session, actor_user_id: UUID, booking_id: UUID, new_start_time: datetime
) -> BookingModel:
    """Shift a whole booking to a new start, keeping each leg's length and the
    gaps between legs. Re-checks opening hours and, for any already-named
    technician, their timeline (this booking excluded), slot locks and leave."""
    booking = db.get(BookingModel, booking_id)
    if booking is None:
        raise AppointmentNotFoundError()
    if booking.status not in RESCHEDULABLE_STATUSES:
        raise AppointmentError("Only pending or approved bookings can be moved")

    details = (
        db.query(BookingDetailModel)
        .filter(BookingDetailModel.booking_id == booking_id)
        .order_by(BookingDetailModel.start_time)
        .all()
    )
    if not details:
        raise AppointmentError("This booking has no items to move")

    new_start_time = new_start_time.astimezone(timezone.utc)
    day = new_start_time.astimezone(shop_timezone()).date()
    open_utc, _ = opening_window_utc(day)
    if new_start_time < open_utc or new_start_time > last_booking_utc(day):
        raise AppointmentError("Bookings start between opening and the last booking time")

    shift = new_start_time - details[0].start_time
    own_windows = {(d.start_time, d.end_time) for d in details}
    day_start, day_end = day_bounds_utc(day)
    day_locks = locks_overlapping(db, booking.branch_id, day_start, day_end)

    for detail in details:
        new_start = detail.start_time + shift
        new_end = detail.end_time + shift
        if detail.staff_id is None:
            continue
        busy = [w for w in staff_timeline_busy(db, detail.staff_id, day) if w not in own_windows]
        for lock in day_locks:
            if lock.staff_id is None or lock.staff_id == detail.staff_id:
                busy.append((lock.start_time, lock.end_time))
        busy.extend(leaves_for_day(db, [detail.staff_id], day).get(detail.staff_id, []))
        if any(b_start < new_end and b_end > new_start for b_start, b_end in busy):
            raise AppointmentConflictError()

    for detail in details:
        detail.start_time = detail.start_time + shift
        detail.end_time = detail.end_time + shift
    booking.booking_date = day
    db.add(
        AuditLogModel(
            actor_user_id=actor_user_id,
            action="schedule.appointment_moved",
            entity_type="booking",
            entity_id=booking.id,
            details={"new_start_time": new_start_time.isoformat()},
        )
    )
    db.commit()
    db.refresh(booking)
    return booking
