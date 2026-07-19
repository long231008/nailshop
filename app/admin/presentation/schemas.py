from uuid import UUID

from pydantic import BaseModel


class StaffActionResponse(BaseModel):
    staff_id: UUID
    status: str
    reassigned_bookings: int = 0
