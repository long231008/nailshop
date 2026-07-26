from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class MyProfileResponse(BaseModel):
    id: UUID
    phone_number: str | None
    email: str | None
    first_name: str | None
    surname: str | None
    role: str
    status: str
    created_at: datetime


class MyProfileUpdateRequest(BaseModel):
    phone_number: str | None = Field(default=None, pattern=r"^\+?[0-9]{6,15}$")
    email: EmailStr | None = None
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    surname: str | None = Field(default=None, min_length=1, max_length=100)


class MyBookingSummary(BaseModel):
    id: UUID
    branch_id: UUID
    booking_date: date
    status: str
    total_price: float | None
    deposit_amount: float | None = None
    # Present while an approved booking is waiting for its deposit.
    deposit_link: str | None = None


class MyCustomDesignSummary(BaseModel):
    id: UUID
    image_url: str
    description: str | None
    estimated_price: float | None
    status: str
