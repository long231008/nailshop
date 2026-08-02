"""Create an advance booking on the capacity ledger (doc 3.3b).

The customer commits to *salon + time + services + price*. Every item keeps
staff_id NULL and holds the cautious planning duration; the nightly allocation
names the technician and shrinks each leg to that tech's real minutes. Naming
a technician records a *note for the salon*: the allocator ignores it and
stays free to optimise, and a manager grants the wish by hand afterwards
when the finished schedule allows (/allocation/reassign).
"""

import logging
from datetime import timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.allocation.application.roster import expected_staff
from app.availability.application.capacity import (
    BookingWindow,
    CapacityLedger,
    booking_window_state,
    eligible_staff,
    matrix_configured_for,
    planning_minutes,
    rare_service_cap,
)
from app.bookings.application.exceptions import (
    DailyBookingLimitExceededError,
    InvalidBookingItemsError,
    StaffConflictError,
)
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.bookings.presentation.schemas import BookingCreateRequest
from app.branches.infrastructure.models import LocationModel
from app.capability.application.matrix import ceil_to_grid, is_available, load_matrix
from app.custom_designs.infrastructure.models import CustomDesignModel, CustomDesignStatus
from app.discounts.application.gift import find_gift_message
from app.services.infrastructure.models import ServiceExtensionModel, ServiceModel
from app.shared.infrastructure.clock import last_booking_utc, opening_window_utc, shop_timezone
from app.slot_locks.application.locks import is_interval_locked
from app.staff.infrastructure.models import StaffModel, StaffStatus

logger = logging.getLogger(__name__)

DAILY_LIMIT_MINUTES = 120
# Custom designs are nail art: their quote may only ever be spent on a nail art
# service, never on an unrelated (and more expensive) treatment.
NAIL_ART_CATEGORY = "addon"
ACTIVE_BOOKING_STATUSES = [
    BookingStatus.PENDING,
    BookingStatus.APPROVED,
    BookingStatus.IN_PROGRESS,
    BookingStatus.COMPLETED,
]

WINDOW_MESSAGES = {
    BookingWindow.CLOSED: (
        "Bookings for this day have closed (they close at 21:00 the evening "
        "before). Please pick a later day, or contact the salon."
    ),
    BookingWindow.TOO_FAR: "This date is beyond the booking horizon",
    BookingWindow.CLOSED_DAY: (
        "We are closed on Sundays. For a Sunday appointment, please message us "
        "on our Facebook page."
    ),
}


def _lock_branch(db: Session, branch_id: UUID) -> None:
    """Serialise booking creation per branch.

    Checking capacity and inserting the rows are separate statements; without
    this lock two concurrent requests both see a free lane and both take it.
    """
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key)::bigint)"),
        {"key": f"booking:branch:{branch_id}"},
    )


def create_booking(
    db: Session, customer_id: UUID, payload: BookingCreateRequest
) -> tuple[BookingModel, str | None]:
    booking_start = payload.items[0].start_time.astimezone(timezone.utc)
    booking_date = booking_start.astimezone(shop_timezone()).date()

    if db.get(LocationModel, payload.branch_id) is None:
        raise InvalidBookingItemsError("Branch not found")

    window = booking_window_state(booking_date)
    if window != BookingWindow.OPEN:
        raise InvalidBookingItemsError(WINDOW_MESSAGES[window])

    open_utc, _ = opening_window_utc(booking_date)
    if not (open_utc <= booking_start <= last_booking_utc(booking_date)):
        raise InvalidBookingItemsError("Bookings start between 09:00 and 17:30")

    _lock_branch(db, payload.branch_id)

    ledger = CapacityLedger(db, payload.branch_id, booking_date)

    prepared_items = []
    capacity_legs = []
    service_caps: dict[UUID, int] = {}
    total_duration = 0
    total_price = Decimal("0")
    cursor = booking_start

    for item in payload.items:
        service = db.get(ServiceModel, item.service_id)
        if service is None:
            raise InvalidBookingItemsError(f"Service {item.service_id} not found")
        if service.branch_id is not None and service.branch_id != payload.branch_id:
            raise InvalidBookingItemsError(
                f"Service {item.service_id} is not offered at this branch"
            )

        price = Decimal(str(service.base_price))
        extra_minutes = 0

        if item.service_extension_id is not None:
            extension = db.get(ServiceExtensionModel, item.service_extension_id)
            if extension is None or extension.service_id != service.id:
                raise InvalidBookingItemsError(
                    f"Service extension {item.service_extension_id} is invalid for this service"
                )
            extra_minutes = extension.extra_duration_min
            price += Decimal(str(extension.extra_price))

        if item.custom_design_id is not None:
            design = db.get(CustomDesignModel, item.custom_design_id)
            if design is None or design.customer_id != customer_id:
                raise InvalidBookingItemsError("Custom design not found")
            if service.category != NAIL_ART_CATEGORY:
                raise InvalidBookingItemsError(
                    "A custom design can only be booked as a nail art service"
                )
            if design.status != CustomDesignStatus.PRICED:
                raise InvalidBookingItemsError(
                    "This design has no accepted quote or is already booked"
                )
            if any(p.get("custom_design_id") == design.id for p in prepared_items):
                raise InvalidBookingItemsError("This design is already in the booking")
            # The agreed quote replaces the service's base price.
            price = Decimal(str(design.estimated_price))
            design.status = CustomDesignStatus.ACCEPTED

        preferred_staff_id = None
        if item.staff_id is not None:
            # Preferred technician (doc 3.3b, softened): a note for the salon,
            # never an input to the allocator. Only wishes that can possibly
            # come true are accepted, so the manager reviewing them later is
            # not chasing the impossible.
            staff = (
                db.query(StaffModel)
                .filter(
                    StaffModel.id == item.staff_id,
                    StaffModel.status == StaffStatus.ACTIVE,
                )
                .first()
            )
            if staff is None or not is_available(staff, booking_date):
                raise InvalidBookingItemsError(
                    "The requested staff member is not working on this day"
                )
            matrix = load_matrix(db)
            staff_cells = matrix.get(staff.id, {})
            if service.id not in staff_cells and (
                staff_cells
                or matrix_configured_for(
                    expected_staff(db, payload.branch_id, booking_date), matrix
                )
            ):
                raise InvalidBookingItemsError(
                    "The requested staff member does not offer this service"
                )
            preferred_staff_id = staff.id

        # Every visit holds a lane with the cautious planning duration; the
        # nightly allocation picks the tech and shrinks the leg (doc 4.3).
        minutes = planning_minutes(db, payload.branch_id, service.id, booking_date)
        if minutes is None:
            raise InvalidBookingItemsError(
                f"'{service.name}' is not available on this day"
            )
        duration_min = ceil_to_grid(minutes + extra_minutes)
        start_time = cursor
        end_time = start_time + timedelta(minutes=duration_min)
        if is_interval_locked(db, payload.branch_id, None, start_time, end_time):
            raise StaffConflictError()
        capacity_legs.append(
            {
                "service_id": service.id,
                "skill_group": service.skill_group,
                "start": start_time,
                "end": end_time + timedelta(minutes=service.buffer_after_min),
            }
        )
        service_caps[service.id] = rare_service_cap(
            len(eligible_staff(db, payload.branch_id, service.id, booking_date))
        )

        # Every leg must still *start* within booking hours; a long visit may
        # then run past closing - that's the salon's explicit choice.
        if start_time > last_booking_utc(booking_date):
            raise InvalidBookingItemsError("This visit would run past closing time")

        prepared_items.append(
            {
                "service_id": service.id,
                "service_extension_id": item.service_extension_id,
                "preferred_staff_id": preferred_staff_id,
                "custom_design_id": item.custom_design_id,
                "start_time": start_time,
                "end_time": end_time,
                "duration_min": duration_min,
                "price": price,
            }
        )
        total_duration += duration_min
        total_price += price
        cursor = end_time  # items run back-to-back: one customer, one chair

    if capacity_legs and not ledger.fits(capacity_legs, service_caps):
        raise InvalidBookingItemsError(
            "This time no longer has room - please pick another time"
        )

    existing_minutes = (
        db.query(func.coalesce(func.sum(BookingDetailModel.duration_min), 0))
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .filter(
            BookingModel.customer_id == customer_id,
            BookingModel.booking_date == booking_date,
            BookingModel.status.in_(ACTIVE_BOOKING_STATUSES),
        )
        .scalar()
    )
    if existing_minutes + total_duration > DAILY_LIMIT_MINUTES:
        raise DailyBookingLimitExceededError()

    booking = BookingModel(
        customer_id=customer_id,
        branch_id=payload.branch_id,
        booking_date=booking_date,
        status=BookingStatus.PENDING,
        total_price=total_price,
    )
    db.add(booking)
    db.flush()

    for item in prepared_items:
        db.add(BookingDetailModel(booking_id=booking.id, **item))

    db.commit()
    db.refresh(booking)

    gift_message = find_gift_message(db, payload.branch_id, total_price)
    if gift_message is not None:
        logger.info(
            "Customer %s qualifies for a shop gift (booking %s total %s)",
            customer_id,
            booking.id,
            total_price,
        )

    return booking, gift_message
