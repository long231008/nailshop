from datetime import timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.allocation.application.roster import release_pin_if_unused
from app.audit_log.infrastructure.models import AuditLogModel
from app.bookings.application.exceptions import (
    BookingNotFoundError,
    InvalidBookingStateError,
    NotBookingOwnerError,
)
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.custom_designs.infrastructure.models import CustomDesignModel, CustomDesignStatus
from app.shared.infrastructure.clock import now_utc, shop_timezone
from app.webhooks.infrastructure.models import (
    PaymentTransactionModel,
    PaymentTransactionStatus,
    PaymentTransactionType,
)


def release_designs(db: Session, booking_id) -> None:
    """A cancelled booking frees its custom designs so they can be rebooked."""
    details = (
        db.query(BookingDetailModel)
        .filter(
            BookingDetailModel.booking_id == booking_id,
            BookingDetailModel.custom_design_id.isnot(None),
        )
        .all()
    )
    for detail in details:
        design = db.get(CustomDesignModel, detail.custom_design_id)
        if design is not None and design.status == CustomDesignStatus.ACCEPTED:
            design.status = CustomDesignStatus.PRICED


CANCELLABLE_STATUSES = (BookingStatus.PENDING, BookingStatus.APPROVED)
# Customers cannot cancel at the door; inside this window it becomes a no-show
# decision for the salon instead.
MIN_NOTICE_HOURS = 2


def cancel_booking(db: Session, booking_id: UUID, customer_id: UUID) -> BookingModel:
    booking = db.get(BookingModel, booking_id)
    if booking is None:
        raise BookingNotFoundError()
    if booking.customer_id != customer_id:
        raise NotBookingOwnerError()
    if booking.status not in CANCELLABLE_STATUSES:
        raise InvalidBookingStateError("Only pending or approved bookings can be cancelled")

    first_start = (
        db.query(BookingDetailModel.start_time)
        .filter(BookingDetailModel.booking_id == booking_id)
        .order_by(BookingDetailModel.start_time)
        .limit(1)
        .scalar()
    )
    if first_start is not None and first_start - now_utc() < timedelta(hours=MIN_NOTICE_HOURS):
        raise InvalidBookingStateError(
            f"Bookings can no longer be cancelled less than {MIN_NOTICE_HOURS} hours "
            "before the appointment. Please contact the salon."
        )

    booking.status = BookingStatus.CANCELLED
    # The session runs with autoflush=False: push the CANCELLED status now so
    # the pin-release query below no longer counts this booking as active.
    db.flush()
    release_designs(db, booking.id)
    # Cancelling the last named booking of (tech, day) releases the exclusive
    # pin, so other salons may claim the technician again (doc 3.3b).
    named_details = (
        db.query(BookingDetailModel)
        .filter(
            BookingDetailModel.booking_id == booking.id,
            BookingDetailModel.staff_requested.is_(True),
            BookingDetailModel.staff_id.isnot(None),
        )
        .all()
    )
    for detail in named_details:
        day = detail.start_time.astimezone(shop_timezone()).date()
        release_pin_if_unused(db, detail.staff_id, day)
    # There is no automatic refund: if the deposit was already taken, the audit
    # entry flags it so the salon knows a refund decision is owed to the customer.
    deposit_paid = (
        db.query(PaymentTransactionModel)
        .filter(
            PaymentTransactionModel.booking_id == booking.id,
            PaymentTransactionModel.transaction_type == PaymentTransactionType.DEPOSIT,
            PaymentTransactionModel.status == PaymentTransactionStatus.SUCCESS,
        )
        .first()
        is not None
    )
    db.add(
        AuditLogModel(
            actor_user_id=customer_id,
            action="booking.cancelled_by_customer",
            entity_type="booking",
            entity_id=booking.id,
            details={
                "deposit_paid": deposit_paid,
                "deposit_amount": float(booking.deposit_amount)
                if deposit_paid and booking.deposit_amount is not None
                else None,
            },
        )
    )
    db.commit()
    db.refresh(booking)
    return booking
