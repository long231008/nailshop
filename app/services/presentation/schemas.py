from uuid import UUID

from pydantic import BaseModel


class ServiceCreateRequest(BaseModel):
    branch_id: UUID | None = None
    name: str
    category: str
    duration_min: int
    base_price: float


class ServiceUpdateRequest(BaseModel):
    branch_id: UUID | None = None
    name: str | None = None
    category: str | None = None
    duration_min: int | None = None
    base_price: float | None = None


class ServiceResponse(BaseModel):
    id: UUID
    branch_id: UUID | None
    name: str
    category: str
    duration_min: int
    base_price: float


class ServiceLengthCreateRequest(BaseModel):
    name: str
    extra_price: float = 0
    extra_duration_min: int = 0


class ServiceLengthResponse(BaseModel):
    id: UUID
    service_id: UUID
    name: str
    extra_price: float
    extra_duration_min: int
