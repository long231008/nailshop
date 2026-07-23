from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.bookings.application.create import create_booking
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel
from app.bookings.presentation.schemas import BookingCreateRequest, BookingItemRequest
from app.custom_designs.application.exceptions import (
    CustomDesignAlreadyDecidedError,
    CustomDesignNotFoundError,
    CustomDesignNotOwnedError,
    CustomDesignNotPricedError,
)
from app.custom_designs.infrastructure.models import CustomDesignModel, CustomDesignStatus


def _get_owned_design(db: Session, design_id: UUID, customer_id: UUID) -> CustomDesignModel:
    design = db.get(CustomDesignModel, design_id)
    if design is None:
        raise CustomDesignNotFoundError()
    if design.customer_id != customer_id:
        raise CustomDesignNotOwnedError()
    return design


def accept_custom_design(
    db: Session,
    customer_id: UUID,
    design_id: UUID,
    branch_id: UUID,
    service_id: UUID,
    staff_id: UUID | None,
    start_time: datetime,
) -> tuple[BookingModel, CustomDesignModel, str | None]:
    design = _get_owned_design(db, design_id, customer_id)
    if design.status != CustomDesignStatus.PRICED:
        raise CustomDesignNotPricedError()

    booking_request = BookingCreateRequest(
        branch_id=branch_id,
        items=[
            BookingItemRequest(service_id=service_id, staff_id=staff_id, start_time=start_time)
        ],
    )
    booking, gift_message = create_booking(db, customer_id, booking_request)

    detail = (
        db.query(BookingDetailModel).filter(BookingDetailModel.booking_id == booking.id).first()
    )
    detail.price = float(design.estimated_price)
    detail.custom_design_id = design.id
    booking.total_price = float(design.estimated_price)

    design.status = CustomDesignStatus.ACCEPTED

    db.commit()
    db.refresh(booking)
    db.refresh(design)

    return booking, design, gift_message


def reject_custom_design(db: Session, customer_id: UUID, design_id: UUID) -> CustomDesignModel:
    design = _get_owned_design(db, design_id, customer_id)
    if design.status in (CustomDesignStatus.ACCEPTED, CustomDesignStatus.REJECTED):
        raise CustomDesignAlreadyDecidedError()

    design.status = CustomDesignStatus.REJECTED
    db.commit()
    db.refresh(design)
    return design
