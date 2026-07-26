from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class StaffResponse(BaseModel):
    id: UUID
    user_id: UUID
    branch_id: UUID
    display_name: str
    status: str
    can_lock_slots: bool
    created_at: datetime


class StaffShift(BaseModel):
    id: UUID
    start_time: datetime
    end_time: datetime


class StaffAppointment(BaseModel):
    booking_id: UUID
    booking_detail_id: UUID
    service_id: UUID
    start_time: datetime
    end_time: datetime
    status: str


class StaffScheduleResponse(BaseModel):
    staff_id: UUID
    shifts: list[StaffShift]
    appointments: list[StaffAppointment]


class StartServiceRequest(BaseModel):
    booking_detail_id: UUID
