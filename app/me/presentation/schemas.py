from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class MyProfileResponse(BaseModel):
    id: UUID
    phone_number: str | None
    email: str | None
    role: str
    status: str
    created_at: datetime


class MyProfileUpdateRequest(BaseModel):
    phone_number: str | None = None
    email: str | None = None


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
