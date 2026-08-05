from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class DailyAppointment(BaseModel):
    booking_id: UUID
    branch_id: UUID
    start_time: datetime
    end_time: datetime
    service_name: str
    staff_name: str | None
    customer_name: str | None
    customer_phone: str | None
    price: float
    status: str
    # Present when the appointment carries the customer's own nail art.
    design_image_url: str | None = None
    design_description: str | None = None


class PendingAppointment(DailyAppointment):
    # awaiting_approval: needs an admin/staff grant.
    # awaiting_deposit: granted, deposit not received yet.
    stage: str


class DailyScheduleResponse(BaseModel):
    date: date
    appointment_count: int
    # Revenue is management information: admins get the figure, staff get null.
    expected_value: float | None
    appointments: list[DailyAppointment]
    pending: list[PendingAppointment]


class AddAppointmentRequest(BaseModel):
    branch_id: UUID
    service_ids: list[UUID] = Field(min_length=1)
    start_time: datetime
    # Name the technician now, or leave null for the nightly allocation to fill.
    staff_id: UUID | None = None
    # Either an existing customer, or a phone number the desk uses to create one.
    customer_id: UUID | None = None
    customer_phone: str | None = Field(default=None, pattern=r"^\+?[0-9]{6,15}$")
    customer_name: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def require_customer(self) -> "AddAppointmentRequest":
        if self.customer_id is None and not self.customer_phone:
            raise ValueError("customer_id or customer_phone is required")
        return self


class RescheduleAppointmentRequest(BaseModel):
    new_start_time: datetime


class AppointmentMutationResponse(BaseModel):
    booking_id: UUID
    status: str
    booking_date: date
    staff_id: UUID | None
    start_time: datetime
    end_time: datetime


class DeskSlot(BaseModel):
    start_time: datetime
    end_time: datetime
    # Leaves the salon's day without a gap. Every listed time can be booked;
    # this one just costs the shop nothing.
    recommended: bool = False


class DeskSlotsResponse(BaseModel):
    date: date
    slots: list[DeskSlot]


class SheetAppointment(BaseModel):
    booking_id: UUID
    start_time: datetime
    end_time: datetime
    service_name: str
    customer_name: str | None
    customer_phone: str | None
    price: float
    status: str
    design_image_url: str | None = None
    design_description: str | None = None


class DaySheet(BaseModel):
    staff_id: UUID
    staff_name: str
    # Which shop to turn up at, from the nightly roster.
    branch_id: UUID | None
    branch_name: str | None
    appointment_count: int
    working_minutes: int
    first_start: datetime | None
    last_end: datetime | None
    appointments: list[SheetAppointment]


class DaySheetsResponse(BaseModel):
    date: date
    # False until the 21:00 run has closed this day, when the sheets are still
    # provisional - only named technicians appear on them.
    allocated: bool
    sheets: list[DaySheet]
