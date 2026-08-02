from uuid import UUID

from pydantic import BaseModel


class BranchCreateRequest(BaseModel):
    name: str
    address: str | None = None
    phone_number: str | None = None


class BranchUpdateRequest(BaseModel):
    name: str | None = None
    address: str | None = None
    phone_number: str | None = None


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
    services: list[BranchServiceSummary] = []


class BranchTechnicianSummary(BaseModel):
    """The public face of a technician: just enough for a customer to name
    their wish when booking (preferred technician, granted by hand later)."""

    id: UUID
    display_name: str
