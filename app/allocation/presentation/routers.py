from datetime import date as date_type
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.allocation.application.materialize import materialize_day, release_staff_assignments
from app.allocation.application.roster import solve_day
from app.allocation.infrastructure.assignments import StaffDayAssignmentModel
from app.allocation.infrastructure.models import AllocationRunModel
from app.allocation.presentation.schemas import (
    AllocationRunRequest,
    AllocationRunResponse,
    AllocationStatusResponse,
    RosterEntry,
    UnassignedLeg,
)
from app.auth.domain.value_object import UserRole
from app.availability.application.capacity import ACTIVE_BOOKING_STATUSES
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel
from app.branches.infrastructure.models import LocationModel
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import day_bounds_utc
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import require_roles
from app.staff.infrastructure.models import StaffModel

router = APIRouter(prefix="/allocation", tags=["allocation"])


def _run_response(run: AllocationRunModel) -> AllocationRunResponse:
    return AllocationRunResponse(
        id=run.id,
        branch_id=run.branch_id,
        target_date=run.target_date,
        assigned_count=run.assigned_count,
        unassigned_count=run.unassigned_count,
        created_at=run.created_at,
    )


@router.post("/run", response_model=list[AllocationRunResponse])
def run_allocation(
    payload: AllocationRunRequest,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> list[AllocationRunResponse]:
    if payload.branch_id is not None:
        if db.get(LocationModel, payload.branch_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")
        branch_ids = [payload.branch_id]
    else:
        branch_ids = [branch.id for branch in db.query(LocationModel).all()]

    # Step A first: every tech gets a branch for the day (pins untouched).
    solve_day(db, payload.target_date)

    runs = []
    for branch_id in branch_ids:
        if payload.release_staff_id is not None:
            release_staff_assignments(
                db, payload.release_staff_id, branch_id, payload.target_date
            )
        runs.append(materialize_day(db, branch_id, payload.target_date))
    return [_run_response(run) for run in runs]


@router.get("/status", response_model=AllocationStatusResponse)
def allocation_status(
    target_date: date_type,
    branch_id: UUID | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> AllocationStatusResponse:
    runs_query = db.query(AllocationRunModel).filter(
        AllocationRunModel.target_date == target_date
    )
    if branch_id is not None:
        runs_query = runs_query.filter(AllocationRunModel.branch_id == branch_id)
    runs = runs_query.order_by(AllocationRunModel.created_at.desc()).all()

    roster_query = (
        db.query(StaffDayAssignmentModel, StaffModel.display_name, LocationModel.name)
        .join(StaffModel, StaffDayAssignmentModel.staff_id == StaffModel.id)
        .join(LocationModel, StaffDayAssignmentModel.branch_id == LocationModel.id)
        .filter(StaffDayAssignmentModel.day == target_date)
    )
    if branch_id is not None:
        roster_query = roster_query.filter(StaffDayAssignmentModel.branch_id == branch_id)
    roster = [
        RosterEntry(
            staff_id=assignment.staff_id,
            staff_name=staff_name,
            branch_id=assignment.branch_id,
            branch_name=branch_name,
            source=assignment.source.value,
        )
        for assignment, staff_name, branch_name in roster_query.order_by(
            LocationModel.name, StaffModel.display_name
        ).all()
    ]

    day_start, day_end = day_bounds_utc(target_date)
    unassigned_query = (
        db.query(BookingDetailModel, ServiceModel.name)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .join(ServiceModel, BookingDetailModel.service_id == ServiceModel.id)
        .filter(
            BookingModel.status.in_(ACTIVE_BOOKING_STATUSES),
            BookingDetailModel.staff_id.is_(None),
            BookingDetailModel.start_time >= day_start,
            BookingDetailModel.start_time < day_end,
        )
    )
    if branch_id is not None:
        unassigned_query = unassigned_query.filter(BookingModel.branch_id == branch_id)

    unassigned = [
        UnassignedLeg(
            booking_id=detail.booking_id,
            service_name=service_name,
            start_time=detail.start_time,
            end_time=detail.end_time,
        )
        for detail, service_name in unassigned_query.order_by(
            BookingDetailModel.start_time
        ).all()
    ]
    return AllocationStatusResponse(
        runs=[_run_response(run) for run in runs], roster=roster, unassigned=unassigned
    )

