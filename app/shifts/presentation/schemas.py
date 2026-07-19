from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, model_validator


class ShiftCreateRequest(BaseModel):
    staff_id: UUID
    branch_id: UUID
    start_time: datetime
    end_time: datetime

    @model_validator(mode="after")
    def validate_time_range(self) -> "ShiftCreateRequest":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class ShiftResponse(BaseModel):
    id: UUID
    staff_id: UUID
    branch_id: UUID
    start_time: datetime
    end_time: datetime
