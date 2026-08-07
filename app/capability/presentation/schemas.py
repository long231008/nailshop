from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


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
    # False pins the technician to their home branch every day.
    floating: bool
    display_name: str
    status: str
    days_off: str
    # Null on both means full time - in for the whole of the shop's opening.
    work_start_hour: int | None
    work_end_hour: int | None
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
    # Part-time hours, shop-local. Leave both null for a full-timer.
    work_start_hour: int | None = Field(default=None, ge=0, le=23)
    work_end_hour: int | None = Field(default=None, ge=1, le=24)
    max_hours_week: int = Field(default=40, ge=0, le=80)
    # Placement: home branch + whether the nightly plan may move them away
    # from it. Applied as a pair, and only when the request sends them -
    # older callers saving hours must not silently unpin anyone.
    branch_id: UUID | None = None
    floating: bool = True

    @model_validator(mode="after")
    def hours_make_a_window(self) -> "StaffSettingsItem":
        if (
            self.work_start_hour is not None
            and self.work_end_hour is not None
            and self.work_end_hour <= self.work_start_hour
        ):
            raise ValueError("work_end_hour must be after work_start_hour")
        return self

    @model_validator(mode="after")
    def a_pin_needs_a_home(self) -> "StaffSettingsItem":
        if not self.floating and self.branch_id is None:
            raise ValueError("A pinned technician needs a home branch")
        return self


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
