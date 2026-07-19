from datetime import date
from uuid import UUID

from pydantic import BaseModel


class MyBookingSummary(BaseModel):
    id: UUID
    branch_id: UUID
    booking_date: date
    status: str
    total_price: float | None
