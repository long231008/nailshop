from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, model_validator

from app.discounts.infrastructure.models import DiscountType


class DiscountCreateRequest(BaseModel):
    name: str
    discount_type: DiscountType
    value: float
    branch_id: UUID | None = None
    service_id: UUID | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None

    @model_validator(mode="after")
    def validate_value(self) -> "DiscountCreateRequest":
        if self.value <= 0:
            raise ValueError("value must be greater than 0")
        if self.discount_type == DiscountType.PERCENTAGE and self.value > 100:
            raise ValueError("percentage value cannot exceed 100")
        if (
            self.start_at is not None
            and self.end_at is not None
            and self.end_at <= self.start_at
        ):
            raise ValueError("end_at must be after start_at")
        return self


class DiscountResponse(BaseModel):
    id: UUID
    name: str
    discount_type: DiscountType
    value: float
    branch_id: UUID | None
    service_id: UUID | None
    start_at: datetime | None
    end_at: datetime | None
    is_active: bool
