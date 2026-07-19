from uuid import UUID

from pydantic import BaseModel


class BranchCreateRequest(BaseModel):
    name: str
    address: str
    phone_number: str | None = None


class BranchServiceSummary(BaseModel):
    id: UUID
    name: str
    category: str
    duration_min: int
    base_price: float


class BranchResponse(BaseModel):
    id: UUID
    name: str
    address: str
    phone_number: str | None
    services: list[BranchServiceSummary] = []
