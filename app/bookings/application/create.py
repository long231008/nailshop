import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.bookings.application.exceptions import (
    DailyBookingLimitExceededError,
    InvalidBookingItemsError,
    StaffConflictError,
)
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.bookings.presentation.schemas import BookingCreateRequest
from app.discounts.infrastructure.models import DiscountModel, DiscountType
from app.services.infrastructure.models import ServiceExtensionModel, ServiceModel

logger = logging.getLogger(__name__)

DAILY_LIMIT_MINUTES = 120
GIFT_MESSAGE = "Congratulations! Your order qualifies for a free gift from our shop."


def _find_gift_message(db: Session, branch_id: UUID, total_price: float) -> str | None:
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
    booking_date = payload.items[0].start_time.date()

    prepared_items = []
    total_duration = 0
    total_price = 0.0

    for item in payload.items:
        if item.start_time.date() != booking_date:
            raise InvalidBookingItemsError("All items in a booking must be on the same day")

        service = db.get(ServiceModel, item.service_id)
        if service is None:
            raise InvalidBookingItemsError(f"Service {item.service_id} not found")

        duration_min = service.duration_min
        price = float(service.base_price)

        if item.service_extension_id is not None:
            extension = db.get(ServiceExtensionModel, item.service_extension_id)
            if extension is None or extension.service_id != service.id:
                raise InvalidBookingItemsError(
                    f"Service extension {item.service_extension_id} is invalid for this service"
                )
            duration_min += extension.extra_duration_min
            price += float(extension.extra_price)

        end_time = item.start_time + timedelta(minutes=duration_min)

        if item.staff_id is not None:
            conflict = (
                db.query(BookingDetailModel)
                .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
                .filter(
                    BookingDetailModel.staff_id == item.staff_id,
                    BookingModel.status.notin_([BookingStatus.CANCELLED, BookingStatus.NO_SHOW]),
                    BookingDetailModel.start_time < end_time,
                    BookingDetailModel.end_time > item.start_time,
                )
                .first()
            )
            if conflict is not None:
                raise StaffConflictError()

        prepared_items.append(
            {
                "service_id": service.id,
                "service_extension_id": item.service_extension_id,
                "staff_id": item.staff_id,
                "start_time": item.start_time,
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
            BookingModel.status.notin_([BookingStatus.CANCELLED, BookingStatus.NO_SHOW]),
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
            "Customer %s qualifies for a shop gift (booking %s total %.2f)",
            customer_id,
            booking.id,
            total_price,
        )

    return booking, gift_message
