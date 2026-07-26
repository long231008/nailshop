from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class SlotLockCreateRequest(BaseModel):
    branch_id: UUID
    # Omit staff_id to lock the whole branch for this time range.
    staff_id: UUID | None = None
    start_time: datetime
    end_time: datetime
    reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_time_range(self) -> "SlotLockCreateRequest":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class SlotLockResponse(BaseModel):
    id: UUID
    branch_id: UUID
    staff_id: UUID | None
    start_time: datetime
    end_time: datetime
    reason: str | None
    created_by: UUID | None
