from uuid import UUID

from pydantic import BaseModel


class PaymentWebhookPayload(BaseModel):
    transaction_id: str
    booking_id: UUID
    amount: float
    transaction_type: str
