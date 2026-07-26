from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


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
