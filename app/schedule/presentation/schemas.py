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


class DailyScheduleResponse(BaseModel):
    date: date
    appointment_count: int
    expected_value: float
    appointments: list[DailyAppointment]
