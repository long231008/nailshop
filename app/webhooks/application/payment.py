import hashlib
import hmac
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.audit_log.infrastructure.models import AuditLogModel
from app.bookings.infrastructure.models import BookingModel, BookingStatus
from app.shared.infrastructure.clock import now_utc
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


class AmountMismatchError(Exception):
    def __init__(self, expected: Decimal, received: Decimal):
        self.expected = expected
        self.received = received
        super().__init__(f"expected {expected}, received {received}")


def verify_signature(raw_body: bytes, signature: str) -> None:
    expected = hmac.new(
        settings.PAYMENT_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise InvalidSignatureError()


def _expected_amount(
    booking: BookingModel, transaction_type: PaymentTransactionType
) -> Decimal | None:
    if transaction_type == PaymentTransactionType.DEPOSIT:
        return Decimal(str(booking.deposit_amount)) if booking.deposit_amount is not None else None
    if transaction_type == PaymentTransactionType.FINAL_PAYMENT:
        total = booking.final_price if booking.final_price is not None else booking.total_price
        if total is None:
            return None
        deposit = Decimal(str(booking.deposit_amount or 0))
        return Decimal(str(total)) - deposit
    return None


def process_payment_webhook(
    db: Session,
    transaction_id: str,
    booking_id: UUID,
    amount: float,
    transaction_type: PaymentTransactionType,
    succeeded: bool,
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

    received = Decimal(str(amount))
    status = PaymentTransactionStatus.SUCCESS if succeeded else PaymentTransactionStatus.FAILED

    # A "successful" payment that does not cover what is owed must not unlock the
    # booking. It is recorded as FAILED so there is still a trace of the attempt.
    if succeeded:
        expected = _expected_amount(booking, transaction_type)
        if expected is not None and received < expected:
            transaction = PaymentTransactionModel(
                booking_id=booking_id,
                provider_transaction_id=transaction_id,
                amount=received,
                transaction_type=transaction_type,
                status=PaymentTransactionStatus.FAILED,
            )
            db.add(transaction)
            db.commit()
            raise AmountMismatchError(expected, received)

    transaction = PaymentTransactionModel(
        booking_id=booking_id,
        provider_transaction_id=transaction_id,
        amount=received,
        transaction_type=transaction_type,
        status=status,
    )
    db.add(transaction)

    # A deposit that arrives while the booking is still pending confirms it on the
    # spot - the customer has already put money down. Only when deposit_amount is
    # known, so the amount check above has actually run.
    if (
        status == PaymentTransactionStatus.SUCCESS
        and transaction_type == PaymentTransactionType.DEPOSIT
        and booking.status == BookingStatus.PENDING
        and booking.deposit_amount is not None
    ):
        booking.status = BookingStatus.APPROVED
        booking.approved_at = now_utc()
        db.add(
            AuditLogModel(
                actor_user_id=None,
                action="booking.approved",
                entity_type="booking",
                entity_id=booking.id,
                details={"reason": "deposit received while booking was pending"},
            )
        )

    db.commit()
    db.refresh(transaction)
    return transaction, status == PaymentTransactionStatus.SUCCESS
