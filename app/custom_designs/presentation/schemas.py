from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CustomDesignResponse(BaseModel):
    id: UUID
    customer_id: UUID
    image_url: str | None
    description: str | None
    estimated_price: float | None
    status: str


class CustomDesignPriceRequest(BaseModel):
    estimated_price: float = Field(gt=0)


class AcceptCustomDesignRequest(BaseModel):
    branch_id: UUID
    service_id: UUID
    staff_id: UUID | None = None
    start_time: datetime


class AcceptCustomDesignResponse(BaseModel):
    design_id: UUID
    booking_id: UUID
    booking_status: str
    price: float
    gift_message: str | None = None
