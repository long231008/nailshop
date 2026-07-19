from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AvailableSlot(BaseModel):
    staff_id: UUID
    start_time: datetime
    end_time: datetime
