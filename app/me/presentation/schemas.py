from datetime import date
from uuid import UUID

from pydantic import BaseModel


class MyBookingSummary(BaseModel):
    id: UUID
    branch_id: UUID
    booking_date: date
    status: str
    total_price: float | None


class MyCustomDesignSummary(BaseModel):
    id: UUID
    image_url: str
    description: str | None
    estimated_price: float | None
    status: str
