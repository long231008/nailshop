import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from app.bookings.application.exceptions import (
    DailyBookingLimitExceededError,
    InvalidBookingItemsError,
    StaffConflictError,
)
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.bookings.presentation.schemas import BookingCreateRequest
from app.branches.infrastructure.models import LocationModel
from app.discounts.infrastructure.models import DiscountModel, DiscountType
from app.services.infrastructure.models import ServiceExtensionModel, ServiceModel
from app.shifts.infrastructure.models import StaffRosterModel
from app.staff.infrastructure.models import StaffModel, StaffStatus

logger = logging.getLogger(__name__)

DAILY_LIMIT_MINUTES = 120
GIFT_MESSAGE = "Congratulations! Your order qualifies for a free gift from our shop."
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


def _staff_is_on_shift(
    db: Session, staff_id: UUID, start_time: datetime, end_time: datetime
) -> bool:
    shift = (
        db.query(StaffRosterModel.id)
        .filter(
            StaffRosterModel.staff_id == staff_id,
            StaffRosterModel.start_time <= start_time,
            StaffRosterModel.end_time >= end_time,
        )
        .first()
    )
    return shift is not None


def _resolve_staff(
    db: Session,
    branch_id: UUID,
    requested_staff_id: UUID | None,
    start_time: datetime,
    end_time: datetime,
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
        if not _staff_is_on_shift(db, staff.id, start_time, end_time):
            raise StaffConflictError()
        if not _staff_is_free(db, staff.id, start_time, end_time):
            raise StaffConflictError()
        return staff.id

    # No preference: give the customer whoever is genuinely available.
    for staff in candidates.order_by(StaffModel.created_at).all():
        if _staff_is_on_shift(db, staff.id, start_time, end_time) and _staff_is_free(
            db, staff.id, start_time, end_time
        ):
            return staff.id

    raise StaffConflictError()


def _find_gift_message(db: Session, branch_id: UUID, total_price: Decimal) -> str | None:
    now = datetime.now(timezone.utc)
    gift_rule = (
        db.query(DiscountModel)
        .filter(
            DiscountModel.discount_type == DiscountType.GIFT,
            DiscountModel.is_active.is_(True),
            DiscountModel.value < total_price,
            or_(DiscountModel.branch_id.is_(None), DiscountModel.branch_id == branch_id),
            or_(DiscountModel.start_at.is_(None), DiscountModel.start_at <= now),
            or_(DiscountModel.end_at.is_(None), DiscountModel.end_at >= now),
        )
        .order_by(DiscountModel.value.desc())
        .first()
    )
    return GIFT_MESSAGE if gift_rule is not None else None


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
        staff_id = _resolve_staff(db, payload.branch_id, item.staff_id, start_time, end_time)

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

    gift_message = _find_gift_message(db, payload.branch_id, total_price)
    if gift_message is not None:
        logger.info(
            "Customer %s qualifies for a shop gift (booking %s total %s)",
            customer_id,
            booking.id,
            total_price,
        )

    return booking, gift_message
