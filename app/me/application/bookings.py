from uuid import UUID

from sqlalchemy.orm import Session

from app.bookings.infrastructure.models import BookingModel, BookingStatus
from app.webhooks.infrastructure.models import (
    PaymentTransactionModel,
    PaymentTransactionStatus,
    PaymentTransactionType,
)


def list_my_bookings(db: Session, customer_id: UUID) -> list[BookingModel]:
    return (
        db.query(BookingModel)
        .filter(BookingModel.customer_id == customer_id)
        .order_by(BookingModel.created_at.desc())
        .all()
    )


def bookings_awaiting_deposit(db: Session, bookings: list[BookingModel]) -> set[UUID]:
    """IDs of the caller's approved bookings whose deposit has not been paid yet."""
    approved_ids = [b.id for b in bookings if b.status == BookingStatus.APPROVED]
    if not approved_ids:
        return set()

    paid_rows = (
        db.query(PaymentTransactionModel.booking_id)
        .filter(
            PaymentTransactionModel.booking_id.in_(approved_ids),
            PaymentTransactionModel.transaction_type == PaymentTransactionType.DEPOSIT,
            PaymentTransactionModel.status == PaymentTransactionStatus.SUCCESS,
        )
        .all()
    )
    paid_ids = {row[0] for row in paid_rows}
    return set(approved_ids) - paid_ids
