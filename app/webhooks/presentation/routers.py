from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from redis import Redis
from sqlalchemy.orm import Session

from app.shared.infrastructure.cache.redis_client import get_redis
from app.shared.infrastructure.database.session import get_db
from app.webhooks.application.payment import (
    BookingNotFoundError,
    InvalidSignatureError,
    process_payment_webhook,
    verify_signature,
)
from app.webhooks.infrastructure.models import PaymentTransactionType
from app.webhooks.presentation.schemas import PaymentWebhookPayload

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/payment", status_code=status.HTTP_200_OK)
async def payment_webhook(
    request: Request,
    x_signature: str = Header(...),
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
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
        transaction, is_new = process_payment_webhook(
            db, payload.transaction_id, payload.booking_id, payload.amount, transaction_type
        )
    except BookingNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    if is_new and transaction_type == PaymentTransactionType.DEPOSIT:
        redis_client.delete(f"booking:soft_lock:{payload.booking_id}")

    return {
        "status": "ok",
        "transaction_id": transaction.provider_transaction_id,
        "processed": is_new,
    }
