from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AvailableSlot(BaseModel):
    # None until the nightly allocation names the technician ("the salon will
    # assign a suitable technician"); set when the customer asked for someone.
    staff_id: UUID | None
    start_time: datetime
    end_time: datetime


class AvailabilityResponse(BaseModel):
    # "open" | "closed" | "too_far" | "closed_day"
    window: str
    slots: list[AvailableSlot]
