from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class StaffLeaveCreateRequest(BaseModel):
    staff_id: UUID
    start_time: datetime
    end_time: datetime
    reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_time_range(self) -> "StaffLeaveCreateRequest":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class StaffLeaveResponse(BaseModel):
    id: UUID
    staff_id: UUID
    staff_name: str | None
    start_time: datetime
    end_time: datetime
    reason: str | None
    created_by: UUID | None
