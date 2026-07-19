from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BookingItemRequest(BaseModel):
    service_id: UUID
    service_extension_id: UUID | None = None
    staff_id: UUID | None = None
    start_time: datetime


class BookingCreateRequest(BaseModel):
    branch_id: UUID
    items: list[BookingItemRequest] = Field(min_length=1)


class BookingDetailResponse(BaseModel):
    id: UUID
    service_id: UUID
    service_extension_id: UUID | None
    staff_id: UUID | None
    start_time: datetime
    end_time: datetime
    duration_min: int
    price: float
    status: str


class BookingResponse(BaseModel):
    id: UUID
    customer_id: UUID
    branch_id: UUID
    booking_date: date
    status: str
    total_price: float | None
    final_price: float | None
    deposit_amount: float | None
    details: list[BookingDetailResponse] = []


class BookingApproveResponse(BaseModel):
    id: UUID
    status: str
    deposit_amount: float
    deposit_link: str
    soft_lock_expires_in_seconds: int


class BookingStatusResponse(BaseModel):
    id: UUID
    status: str
    total_price: float | None
    final_price: float | None
    deposit_amount: float | None


class BookingActionRequest(BaseModel):
    booking_detail_id: UUID


class FinalPriceRequest(BaseModel):
    final_price: float = Field(gt=0)


class BillResponse(BaseModel):
    id: UUID
    total: float
    deposit: float
    remaining: float
