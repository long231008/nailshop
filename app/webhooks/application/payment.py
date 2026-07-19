import hashlib
import hmac
from uuid import UUID

from sqlalchemy.orm import Session

from app.bookings.infrastructure.models import BookingModel
from app.shared.infrastructure.config.settings import settings
from app.webhooks.infrastructure.models import (
    PaymentTransactionModel,
    PaymentTransactionStatus,
    PaymentTransactionType,
)


class InvalidSignatureError(Exception):
    pass


class BookingNotFoundError(Exception):
    pass


def verify_signature(raw_body: bytes, signature: str) -> None:
    expected = hmac.new(
        settings.PAYMENT_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise InvalidSignatureError()


def process_payment_webhook(
    db: Session,
    transaction_id: str,
    booking_id: UUID,
    amount: float,
    transaction_type: PaymentTransactionType,
) -> tuple[PaymentTransactionModel, bool]:
    existing = (
        db.query(PaymentTransactionModel)
        .filter(PaymentTransactionModel.provider_transaction_id == transaction_id)
        .first()
    )
    if existing is not None:
        return existing, False

    booking = db.get(BookingModel, booking_id)
    if booking is None:
        raise BookingNotFoundError()

    transaction = PaymentTransactionModel(
        booking_id=booking_id,
        provider_transaction_id=transaction_id,
        amount=amount,
        transaction_type=transaction_type,
        status=PaymentTransactionStatus.SUCCESS,
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    return transaction, True
