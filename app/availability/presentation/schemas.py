from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AvailableSlot(BaseModel):
    # None until the nightly allocation names the technician ("the salon will
    # assign a suitable technician"); set when the customer asked for someone.
    staff_id: UUID | None
    start_time: datetime
    end_time: datetime
    # Fits the salon's day without leaving a gap. A hint for the customer -
    # every listed slot is bookable, recommended or not.
    recommended: bool = False


class AvailabilityResponse(BaseModel):
    # "open" | "closed" | "too_far" | "closed_day"
    window: str
    slots: list[AvailableSlot]
