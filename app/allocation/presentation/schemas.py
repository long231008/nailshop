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
    # A tech who called in sick: all their legs are released first, then the
    # day is re-materialized.
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
    # "auto" = placed by Step A of the nightly allocation.
    source: str


class PreferenceEntry(BaseModel):
    """A customer's tech wish next to what the allocator actually did, so a
    manager can grant it by hand (POST /allocation/reassign) when possible."""

    booking_id: UUID
    booking_detail_id: UUID
    service_name: str
    start_time: datetime
    end_time: datetime
    preferred_staff_id: UUID
    preferred_staff_name: str
    assigned_staff_id: UUID | None
    assigned_staff_name: str | None
    honoured: bool


class AllocationStatusResponse(BaseModel):
    runs: list[AllocationRunResponse]
    # Who works where on that day (empty until Step A has run).
    roster: list[RosterEntry]
    unassigned: list[UnassignedLeg]
    preferences: list[PreferenceEntry]


class ReassignRequest(BaseModel):
    booking_detail_id: UUID
    staff_id: UUID


class ReassignResponse(BaseModel):
    booking_detail_id: UUID
    booking_id: UUID
    staff_id: UUID
    staff_name: str
    start_time: datetime
    end_time: datetime
