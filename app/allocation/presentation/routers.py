from datetime import date as date_type
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, aliased

from app.allocation.application.materialize import materialize_day, release_staff_assignments
from app.allocation.application.reassign import (
    LegNotFoundError,
    ReassignConflictError,
    ReassignNotAllowedError,
    reassign_leg,
)
from app.allocation.application.roster import solve_day
from app.allocation.infrastructure.assignments import StaffDayAssignmentModel
from app.allocation.infrastructure.models import AllocationRunModel
from app.allocation.presentation.schemas import (
    AllocationRunRequest,
    AllocationRunResponse,
    AllocationStatusResponse,
    PreferenceEntry,
    ReassignRequest,
    ReassignResponse,
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

    # Step A first: every tech gets a branch for the day.
    solve_day(db, payload.target_date)

    runs = []
    for branch_id in branch_ids:
        if payload.release_staff_id is not None:
            release_staff_assignments(db, payload.release_staff_id, branch_id, payload.target_date)
        runs.append(materialize_day(db, branch_id, payload.target_date))
    return [_run_response(run) for run in runs]


@router.get("/status", response_model=AllocationStatusResponse)
def allocation_status(
    target_date: date_type,
    branch_id: UUID | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> AllocationStatusResponse:
    runs_query = db.query(AllocationRunModel).filter(AllocationRunModel.target_date == target_date)
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
        for detail, service_name in unassigned_query.order_by(BookingDetailModel.start_time).all()
    ]
    # Customer wishes next to the machine's decisions: the allocator ignores
    # preferences on purpose - a manager grants them here, by hand.
    assigned_staff = aliased(StaffModel)
    preferred_staff = aliased(StaffModel)
    preference_query = (
        db.query(
            BookingDetailModel,
            ServiceModel.name,
            preferred_staff.display_name,
            assigned_staff.display_name,
        )
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .join(ServiceModel, BookingDetailModel.service_id == ServiceModel.id)
        .join(preferred_staff, BookingDetailModel.preferred_staff_id == preferred_staff.id)
        .outerjoin(assigned_staff, BookingDetailModel.staff_id == assigned_staff.id)
        .filter(
            BookingModel.status.in_(ACTIVE_BOOKING_STATUSES),
            BookingDetailModel.preferred_staff_id.isnot(None),
            BookingDetailModel.start_time >= day_start,
            BookingDetailModel.start_time < day_end,
        )
    )
    if branch_id is not None:
        preference_query = preference_query.filter(BookingModel.branch_id == branch_id)

    preferences = [
        PreferenceEntry(
            booking_id=detail.booking_id,
            booking_detail_id=detail.id,
            service_name=service_name,
            start_time=detail.start_time,
            end_time=detail.end_time,
            preferred_staff_id=detail.preferred_staff_id,
            preferred_staff_name=preferred_name,
            assigned_staff_id=detail.staff_id,
            assigned_staff_name=assigned_name,
            honoured=detail.staff_id == detail.preferred_staff_id,
        )
        for detail, service_name, preferred_name, assigned_name in preference_query.order_by(
            BookingDetailModel.start_time
        ).all()
    ]

    return AllocationStatusResponse(
        runs=[_run_response(run) for run in runs],
        roster=roster,
        unassigned=unassigned,
        preferences=preferences,
    )


@router.post("/reassign", response_model=ReassignResponse)
def reassign(
    payload: ReassignRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.ADMIN)),
) -> ReassignResponse:
    """Move one leg onto a chosen technician - the manager's tool for granting
    a customer's wish once the allocator has finished."""
    try:
        detail, staff = reassign_leg(
            db, payload.booking_detail_id, payload.staff_id, current_user.id
        )
    except LegNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Booking detail not found"
        )
    except ReassignNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except ReassignConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That technician is not free at this time",
        )
    return ReassignResponse(
        booking_detail_id=detail.id,
        booking_id=detail.booking_id,
        staff_id=staff.id,
        staff_name=staff.display_name,
        start_time=detail.start_time,
        end_time=detail.end_time,
    )
