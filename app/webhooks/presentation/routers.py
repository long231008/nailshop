from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from redis import Redis
from sqlalchemy.orm import Session

from app.bookings.infrastructure.models import BookingDetailModel, BookingModel
from app.notification.application.notify import notify_booking_confirmed, notify_deposit_failed
from app.notification.domain.sender import NotificationSender
from app.notification.infrastructure.senders import get_notification_sender
from app.shared.infrastructure.cache.redis_client import get_redis
from app.shared.infrastructure.clock import shop_timezone
from app.shared.infrastructure.database.session import get_db
from app.webhooks.application.payment import (
    AmountMismatchError,
    BookingNotFoundError,
    BookingNotPayableError,
    InvalidSignatureError,
    RefundExceedsPaymentsError,
    process_payment_webhook,
    verify_signature,
)
from app.webhooks.infrastructure.models import PaymentTransactionStatus, PaymentTransactionType
from app.webhooks.presentation.schemas import PaymentWebhookPayload

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _notify_deposit_outcome(
    db: Session, sender: NotificationSender, booking_id, confirmed: bool
) -> None:
    """Tell the customer on their own channel how their deposit went."""
    booking = db.get(BookingModel, booking_id)
    if booking is None:
        return
    if not confirmed:
        notify_deposit_failed(sender, db, booking.customer_id)
        return

    first_start = (
        db.query(BookingDetailModel.start_time)
        .filter(BookingDetailModel.booking_id == booking_id)
        .order_by(BookingDetailModel.start_time)
        .limit(1)
        .scalar()
    )
    local_start = first_start.astimezone(shop_timezone()) if first_start else None
    notify_booking_confirmed(
        sender,
        db,
        booking.customer_id,
        booking_date=local_start.strftime("%A %d %B %Y")
        if local_start
        else str(booking.booking_date),
        start_time=local_start.strftime("%H:%M") if local_start else "",
    )


@router.post("/payment", status_code=status.HTTP_200_OK)
async def payment_webhook(
    request: Request,
    x_signature: str = Header(...),
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
    notification_sender: NotificationSender = Depends(get_notification_sender),
) -> dict:
    raw_body = await request.body()

    try:
        verify_signature(raw_body, x_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    payload = PaymentWebhookPayload.model_validate_json(raw_body)

    try:
        transaction_type = PaymentTransactionType(payload.transaction_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid transaction_type"
        )

    try:
        transaction, counts_as_paid = process_payment_webhook(
            db,
            payload.transaction_id,
            payload.booking_id,
            payload.amount,
            transaction_type,
            succeeded=payload.status == "success",
        )
    except BookingNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    except AmountMismatchError:
        # The provider took no money for us; the customer needs to try again.
        if transaction_type == PaymentTransactionType.DEPOSIT:
            _notify_deposit_outcome(db, notification_sender, payload.booking_id, confirmed=False)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment amount does not match the amount due",
        )
    except BookingNotPayableError:
        # e.g. a deposit for a booking that was already cancelled by the
        # expiry sweep - the customer must never be told it is confirmed.
        if transaction_type == PaymentTransactionType.DEPOSIT:
            _notify_deposit_outcome(db, notification_sender, payload.booking_id, confirmed=False)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This booking is not accepting payments in its current state",
        )
    except RefundExceedsPaymentsError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Refund exceeds the amount paid for this booking",
        )

    if transaction_type == PaymentTransactionType.DEPOSIT:
        if counts_as_paid:
            redis_client.delete(f"booking:soft_lock:{payload.booking_id}")
            _notify_deposit_outcome(db, notification_sender, payload.booking_id, confirmed=True)
        elif payload.status == "failed":
            _notify_deposit_outcome(db, notification_sender, payload.booking_id, confirmed=False)

    # "processed" reports the stored outcome, not whether this call did new work:
    # a replay of an already-recorded success must keep answering true, or a
    # provider that retries until it sees true would retry forever.
    return {
        "status": "ok",
        "transaction_id": transaction.provider_transaction_id,
        "processed": transaction.status == PaymentTransactionStatus.SUCCESS,
    }
