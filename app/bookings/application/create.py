import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.bookings.application.exceptions import (
    DailyBookingLimitExceededError,
    InvalidBookingItemsError,
    StaffConflictError,
)
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.bookings.presentation.schemas import BookingCreateRequest
from app.branches.infrastructure.models import LocationModel
from app.discounts.application.gift import find_gift_message
from app.services.infrastructure.models import ServiceExtensionModel, ServiceModel
from app.shared.infrastructure.clock import (
    is_closed_day,
    opening_window_utc,
    shop_timezone,
    within_booking_horizon,
)
from app.slot_locks.application.locks import is_interval_locked
from app.staff.infrastructure.models import StaffModel, StaffStatus

logger = logging.getLogger(__name__)

DAILY_LIMIT_MINUTES = 120
# Kept in step with app.availability.application.slot_finder so a slot that was
# offered is a slot that can actually be booked.
BUFFER_MINUTES = 15
ACTIVE_BOOKING_STATUSES = [
    BookingStatus.PENDING,
    BookingStatus.APPROVED,
    BookingStatus.IN_PROGRESS,
    BookingStatus.COMPLETED,
]


def _lock_branch(db: Session, branch_id: UUID) -> None:
    """Serialise booking creation per branch.

    Checking for a clash and inserting the row are two statements; without this lock
    two concurrent requests both see a free slot and both write to it.
    """
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key)::bigint)"),
        {"key": f"booking:branch:{branch_id}"},
    )


def _staff_is_free(db: Session, staff_id: UUID, start_time: datetime, end_time: datetime) -> bool:
    buffer = timedelta(minutes=BUFFER_MINUTES)
    clash = (
        db.query(BookingDetailModel.id)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .filter(
            BookingDetailModel.staff_id == staff_id,
            BookingModel.status.in_(ACTIVE_BOOKING_STATUSES),
            BookingDetailModel.start_time < end_time + buffer,
            BookingDetailModel.end_time > start_time - buffer,
        )
        .first()
    )
    return clash is None


def _within_opening_hours(start_time: datetime, end_time: datetime) -> bool:
    open_utc, close_utc = opening_window_utc(start_time.astimezone(shop_timezone()).date())
    return open_utc <= start_time and end_time <= close_utc


def _overlaps_prepared_items(
    prepared_items: list[dict], staff_id: UUID, start_time: datetime, end_time: datetime
) -> bool:
    """Overlap among items of the SAME request.

    Back-to-back services in one visit are fine (no buffer between them - it is
    the same customer in the chair), but the same staff member cannot do two
    services at literally the same time.
    """
    return any(
        item["staff_id"] == staff_id
        and item["start_time"] < end_time
        and item["end_time"] > start_time
        for item in prepared_items
    )


def _resolve_staff(
    db: Session,
    branch_id: UUID,
    requested_staff_id: UUID | None,
    start_time: datetime,
    end_time: datetime,
    prepared_items: list[dict],
) -> UUID:
    """Pick the staff member who will do the work, or reject the slot."""
    candidates = db.query(StaffModel).filter(
        StaffModel.branch_id == branch_id, StaffModel.status == StaffStatus.ACTIVE
    )
    if requested_staff_id is not None:
        staff = candidates.filter(StaffModel.id == requested_staff_id).first()
        if staff is None:
            raise InvalidBookingItemsError(
                "The requested staff member does not work at this branch"
            )
        if is_interval_locked(db, branch_id, staff.id, start_time, end_time):
            raise StaffConflictError()
        if not _staff_is_free(db, staff.id, start_time, end_time):
            raise StaffConflictError()
        if _overlaps_prepared_items(prepared_items, staff.id, start_time, end_time):
            raise StaffConflictError()
        return staff.id

    # No preference: give the customer whoever is genuinely available.
    for staff in candidates.order_by(StaffModel.created_at).all():
        if (
            not is_interval_locked(db, branch_id, staff.id, start_time, end_time)
            and _staff_is_free(db, staff.id, start_time, end_time)
            and not _overlaps_prepared_items(prepared_items, staff.id, start_time, end_time)
        ):
            return staff.id

    raise StaffConflictError()


def create_booking(
    db: Session, customer_id: UUID, payload: BookingCreateRequest
) -> tuple[BookingModel, str | None]:
    now = datetime.now(timezone.utc)
    booking_date = payload.items[0].start_time.astimezone(timezone.utc).date()

    if db.get(LocationModel, payload.branch_id) is None:
        raise InvalidBookingItemsError("Branch not found")

    _lock_branch(db, payload.branch_id)

    prepared_items = []
    total_duration = 0
    total_price = Decimal("0")

    if not within_booking_horizon(booking_date):
        raise InvalidBookingItemsError("Bookings can be made up to 12 months in advance")
    if is_closed_day(booking_date):
        raise InvalidBookingItemsError(
            "We are closed on Sundays. For a Sunday appointment, please message us "
            "on our Facebook page."
        )

    for item in payload.items:
        start_time = item.start_time.astimezone(timezone.utc)
        if start_time < now:
            raise InvalidBookingItemsError("Bookings cannot be made in the past")
        if start_time.date() != booking_date:
            raise InvalidBookingItemsError("All items in a booking must be on the same day")

        service = db.get(ServiceModel, item.service_id)
        if service is None:
            raise InvalidBookingItemsError(f"Service {item.service_id} not found")
        if service.branch_id is not None and service.branch_id != payload.branch_id:
            raise InvalidBookingItemsError(
                f"Service {item.service_id} is not offered at this branch"
            )

        duration_min = service.duration_min
        price = Decimal(str(service.base_price))

        if item.service_extension_id is not None:
            extension = db.get(ServiceExtensionModel, item.service_extension_id)
            if extension is None or extension.service_id != service.id:
                raise InvalidBookingItemsError(
                    f"Service extension {item.service_extension_id} is invalid for this service"
                )
            duration_min += extension.extra_duration_min
            price += Decimal(str(extension.extra_price))

        end_time = start_time + timedelta(minutes=duration_min)
        if not _within_opening_hours(start_time, end_time):
            raise InvalidBookingItemsError("Bookings must fall within opening hours (09:00-18:00)")
        staff_id = _resolve_staff(
            db, payload.branch_id, item.staff_id, start_time, end_time, prepared_items
        )

        prepared_items.append(
            {
                "service_id": service.id,
                "service_extension_id": item.service_extension_id,
                "staff_id": staff_id,
                "start_time": start_time,
                "end_time": end_time,
                "duration_min": duration_min,
                "price": price,
            }
        )
        total_duration += duration_min
        total_price += price

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
