import hashlib
import hmac
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.audit_log.infrastructure.models import AuditLogModel
from app.bookings.infrastructure.models import BookingModel, BookingStatus
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
    def __init__(self, expected: Decimal | None, received: Decimal):
        self.expected = expected
        self.received = received
        super().__init__(f"expected {expected}, received {received}")


class BookingNotPayableError(Exception):
    """The booking's state does not accept this transaction type."""


class RefundExceedsPaymentsError(Exception):
    """A refund larger than what was actually taken for the booking."""


# Which booking states may take money. A deposit only ever belongs to an
# APPROVED booking (approval is what sets deposit_amount); the balance may be
# settled from approval through completion. Anything cancelled or no-show must
# never be told "payment received" - that money needs a human, not a receipt.
_PAYABLE_STATUSES = {
    PaymentTransactionType.DEPOSIT: (BookingStatus.APPROVED,),
    PaymentTransactionType.FINAL_PAYMENT: (
        BookingStatus.APPROVED,
        BookingStatus.IN_PROGRESS,
        BookingStatus.COMPLETED,
    ),
}


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


def _net_paid(db: Session, booking_id: UUID) -> Decimal:
    """What the salon actually holds for this booking: successful deposits and
    final payments, minus refunds already given back."""
    rows = (
        db.query(PaymentTransactionModel)
        .filter(
            PaymentTransactionModel.booking_id == booking_id,
            PaymentTransactionModel.status == PaymentTransactionStatus.SUCCESS,
        )
        .all()
    )
    net = Decimal("0")
    for row in rows:
        amount = Decimal(str(row.amount))
        if row.transaction_type == PaymentTransactionType.REFUND:
            net -= amount
        else:
            net += amount
    return net


def _validate(
    db: Session,
    booking: BookingModel,
    transaction_type: PaymentTransactionType,
    received: Decimal,
) -> None:
    """Raise if a "successful" transaction must not be recorded as SUCCESS."""
    allowed = _PAYABLE_STATUSES.get(transaction_type)
    if allowed is not None and booking.status not in allowed:
        raise BookingNotPayableError()

    if transaction_type == PaymentTransactionType.REFUND:
        if received > _net_paid(db, booking.id):
            raise RefundExceedsPaymentsError()
        return

    expected = _expected_amount(booking, transaction_type)
    if expected is None or received < expected:
        raise AmountMismatchError(expected, received)

    if received > expected:
        # Overpayment is accepted (tips, rounding at the terminal) but must
        # leave a trace for reconciliation.
        db.add(
            AuditLogModel(
                actor_user_id=None,
                action="payment.overpaid",
                entity_type="booking",
                entity_id=booking.id,
                details={
                    "transaction_type": transaction_type.value,
                    "expected": float(expected),
                    "received": float(received),
                },
            )
        )


def process_payment_webhook(
    db: Session,
    transaction_id: str,
    booking_id: UUID,
    amount: float,
    transaction_type: PaymentTransactionType,
    succeeded: bool,
) -> tuple[PaymentTransactionModel, bool]:
    """Record one provider event. Returns (transaction, newly_paid).

    Idempotency: a transaction_id that was recorded as SUCCESS is immutable -
    the stored outcome is returned and no side effect repeats. A transaction_id
    recorded as FAILED is re-validated against the booking's CURRENT state, so
    a provider that retries the same event after the salon fixed the underlying
    problem (e.g. approved the booking) heals instead of being stuck forever.
    """
    existing = (
        db.query(PaymentTransactionModel)
        .filter(PaymentTransactionModel.provider_transaction_id == transaction_id)
        .first()
    )
    if existing is not None and existing.status == PaymentTransactionStatus.SUCCESS:
        return existing, False

    booking = db.get(BookingModel, booking_id)
    if booking is None:
        raise BookingNotFoundError()

    received = Decimal(str(amount))

    if succeeded:
        try:
            _validate(db, booking, transaction_type, received)
        except (AmountMismatchError, BookingNotPayableError, RefundExceedsPaymentsError):
            # Keep an audit trace of the refused attempt, then tell the
            # provider it did not count.
            if existing is None:
                db.add(
                    PaymentTransactionModel(
                        booking_id=booking_id,
                        provider_transaction_id=transaction_id,
                        amount=received,
                        transaction_type=transaction_type,
                        status=PaymentTransactionStatus.FAILED,
                    )
                )
            db.commit()
            raise

    status = PaymentTransactionStatus.SUCCESS if succeeded else PaymentTransactionStatus.FAILED
    if existing is not None:
        # A FAILED row being retried: the validation above has passed (or the
        # provider now reports failure again) - record the current outcome.
        existing.amount = received
        existing.status = status
        transaction = existing
    else:
        transaction = PaymentTransactionModel(
            booking_id=booking_id,
            provider_transaction_id=transaction_id,
            amount=received,
            transaction_type=transaction_type,
            status=status,
        )
        db.add(transaction)

    db.commit()
    db.refresh(transaction)
    return transaction, status == PaymentTransactionStatus.SUCCESS
