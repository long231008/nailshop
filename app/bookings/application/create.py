from datetime import timedelta
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.bookings.application.exceptions import (
    DailyBookingLimitExceededError,
    InvalidBookingItemsError,
    StaffConflictError,
)
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.bookings.presentation.schemas import BookingCreateRequest
from app.services.infrastructure.models import ServiceExtensionModel, ServiceModel

DAILY_LIMIT_MINUTES = 120


def create_booking(db: Session, customer_id: UUID, payload: BookingCreateRequest) -> BookingModel:
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
    return booking
