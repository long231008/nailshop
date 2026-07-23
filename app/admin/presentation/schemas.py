from uuid import UUID

from pydantic import BaseModel


class StaffActionResponse(BaseModel):
    staff_id: UUID
    status: str
    reassigned_bookings: int = 0


class DesignPricingPermissionRequest(BaseModel):
    enabled: bool


class DesignPricingPermissionResponse(BaseModel):
    staff_id: UUID
    can_price_custom_designs: bool
