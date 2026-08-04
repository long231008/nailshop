from uuid import UUID

from pydantic import BaseModel, Field


class BranchCreateRequest(BaseModel):
    name: str
    address: str | None = None
    phone_number: str | None = None
    # Fixed physical capacity (0 = not tracked / unlimited).
    pedicure_chairs: int = Field(default=0, ge=0)
    manicure_tables: int = Field(default=0, ge=0)
    massage_beds: int = Field(default=0, ge=0)


class BranchUpdateRequest(BaseModel):
    name: str | None = None
    address: str | None = None
    phone_number: str | None = None
    pedicure_chairs: int | None = Field(default=None, ge=0)
    manicure_tables: int | None = Field(default=None, ge=0)
    massage_beds: int | None = Field(default=None, ge=0)


class BranchServiceSummary(BaseModel):
    id: UUID
    name: str
    category: str
    description: str | None
    duration_min: int
    base_price: float


class BranchResponse(BaseModel):
    id: UUID
    name: str
    address: str | None
    phone_number: str | None
    pedicure_chairs: int = 0
    manicure_tables: int = 0
    massage_beds: int = 0
    services: list[BranchServiceSummary] = []
