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
    # Present when this account has a staff profile - lets the staff app find
    # its own schedule and start-service endpoints without admin help.
    staff_id: UUID | None = None
    staff_display_name: str | None = None


class MyProfileUpdateRequest(BaseModel):
    phone_number: str | None = Field(default=None, pattern=r"^\+?[0-9]{6,15}$")
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    surname: str | None = Field(default=None, min_length=1, max_length=100)


class EmailChangeRequest(BaseModel):
    email: EmailStr


class EmailChangeStarted(BaseModel):
    expires_in_seconds: int


class EmailChangeConfirmRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$", examples=["123456"])


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
    image_url: str | None
    description: str | None
    estimated_price: float | None
    status: str
