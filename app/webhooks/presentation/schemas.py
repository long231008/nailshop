from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PaymentWebhookPayload(BaseModel):
    transaction_id: str
    booking_id: UUID
    amount: float = Field(gt=0)
    transaction_type: str
    # Providers that only notify on success can omit this.
    status: Literal["success", "failed"] = "success"
