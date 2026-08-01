from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class MatrixServiceItem(BaseModel):
    id: UUID
    branch_id: UUID | None
    name: str
    category: str
    description: str | None
    duration_min: int
    base_price: float
    skill_group: str
    body_zone: str
    resource: str | None
    turn_weight: float
    buffer_after_min: int


class MatrixStaffItem(BaseModel):
    id: UUID
    # Home branch preference only - technicians belong to the chain.
    branch_id: UUID | None
    display_name: str
    status: str
    days_off: str
    max_hours_week: int


class MatrixResponse(BaseModel):
    services: list[MatrixServiceItem]
    staff: list[MatrixStaffItem]
    # capability[staff_id][service_id] = real minutes
    capability: dict[str, dict[str, int]]


class ServiceUpsertItem(BaseModel):
    id: UUID | None = None
    branch_id: UUID | None = None
    name: str
    category: str = "gel"
    description: str | None = None
    duration_min: int = Field(ge=5, le=480)
    base_price: float = Field(ge=0)
    skill_group: str = "MANI"
    body_zone: str = "HANDS"
    resource: str | None = None
    turn_weight: float = Field(default=1.0, ge=0, le=5)
    buffer_after_min: int = Field(default=0, ge=0, le=60)


class StaffSettingsItem(BaseModel):
    id: UUID
    days_off: str = ""
    max_hours_week: int = Field(default=40, ge=0, le=80)


class MatrixSaveRequest(BaseModel):
    services: list[ServiceUpsertItem]
    staff: list[StaffSettingsItem] = []
    capability: dict[UUID, dict[UUID, int]]


class AffectedBooking(BaseModel):
    booking_id: UUID
    service_id: UUID
    staff_id: UUID
    start_time: datetime


class MatrixSaveResponse(BaseModel):
    warnings: list[str]
    affected_bookings: list[AffectedBooking]
