from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class AllocationRunResponse(BaseModel):
    id: UUID
    branch_id: UUID
    target_date: date
    assigned_count: int
    unassigned_count: int
    created_at: datetime


class AllocationRunRequest(BaseModel):
    target_date: date
    branch_id: UUID | None = None
    # A tech who called in sick: their system-assigned legs are released first,
    # then the day is re-materialized. Customer-requested legs stay put.
    release_staff_id: UUID | None = None


class UnassignedLeg(BaseModel):
    booking_id: UUID
    service_name: str
    start_time: datetime
    end_time: datetime


class RosterEntry(BaseModel):
    staff_id: UUID
    staff_name: str
    branch_id: UUID
    branch_name: str
    # "pin" = a customer booked this tech by name (untouchable);
    # "auto" = placed by Step A of the nightly allocation.
    source: str


class AllocationStatusResponse(BaseModel):
    runs: list[AllocationRunResponse]
    # Who works where on that day (empty until pins exist or Step A has run).
    roster: list[RosterEntry]
    unassigned: list[UnassignedLeg]

