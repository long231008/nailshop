from uuid import UUID

from pydantic import BaseModel, Field


class CustomDesignResponse(BaseModel):
    id: UUID
    customer_id: UUID
    image_url: str
    description: str | None
    estimated_price: float | None


class CustomDesignPriceRequest(BaseModel):
    estimated_price: float = Field(gt=0)
