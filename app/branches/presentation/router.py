from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.branches.infrastructure.models import LocationModel
from app.branches.presentation.schemas import (
    BranchCreateRequest,
    BranchResponse,
    BranchServiceSummary,
    BranchUpdateRequest,
    ServiceLengthSummary,
)
from app.services.infrastructure.models import ServiceExtensionModel, ServiceModel
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import require_roles

router = APIRouter(prefix="/branches", tags=["branches"])


def _branch_response(branch: LocationModel, services: list[BranchServiceSummary]) -> BranchResponse:
    return BranchResponse(
        id=branch.id,
        name=branch.name,
        address=branch.address,
        phone_number=branch.phone_number,
        pedicure_chairs=branch.pedicure_chairs,
        manicure_tables=branch.manicure_tables,
        massage_beds=branch.massage_beds,
        services=services,
    )


def _lengths_by_service(db: Session) -> dict[UUID, list[ServiceLengthSummary]]:
    """Length options grouped by service, shortest first, in one query."""
    grouped: dict[UUID, list[ServiceLengthSummary]] = {}
    for extension in (
        db.query(ServiceExtensionModel).order_by(ServiceExtensionModel.extra_duration_min).all()
    ):
        grouped.setdefault(extension.service_id, []).append(
            ServiceLengthSummary(
                id=extension.id,
                name=extension.name,
                extra_price=float(extension.extra_price),
                extra_duration_min=extension.extra_duration_min,
            )
        )
    return grouped


def _summarise(
    service: ServiceModel, lengths: dict[UUID, list[ServiceLengthSummary]]
) -> BranchServiceSummary:
    return BranchServiceSummary(
        id=service.id,
        name=service.name,
        category=service.category,
        description=service.description,
        duration_min=service.duration_min,
        base_price=float(service.base_price),
        lengths=lengths.get(service.id, []),
    )


@router.post("", response_model=BranchResponse, status_code=status.HTTP_201_CREATED)
def create_branch(
    payload: BranchCreateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> BranchResponse:
    branch = LocationModel(
        name=payload.name,
        address=payload.address,
        phone_number=payload.phone_number,
        pedicure_chairs=payload.pedicure_chairs,
        manicure_tables=payload.manicure_tables,
        massage_beds=payload.massage_beds,
    )
    db.add(branch)
    db.commit()
    db.refresh(branch)

    return _branch_response(branch, [])


@router.get("", response_model=list[BranchResponse])
def list_branches(db: Session = Depends(get_db)) -> list[BranchResponse]:
    branches = db.query(LocationModel).all()
    services = db.query(ServiceModel).all()
    lengths = _lengths_by_service(db)

    services_by_branch: dict = defaultdict(list)
    global_services = []
    for service in services:
        summary = _summarise(service, lengths)
        if service.branch_id is None:
            global_services.append(summary)
        else:
            services_by_branch[service.branch_id].append(summary)

    return [
        _branch_response(branch, services_by_branch.get(branch.id, []) + global_services)
        for branch in branches
    ]


@router.get("/{branch_id}", response_model=BranchResponse)
def get_branch(branch_id: UUID, db: Session = Depends(get_db)) -> BranchResponse:
    branch = db.get(LocationModel, branch_id)
    if branch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")

    services = (
        db.query(ServiceModel)
        .filter(or_(ServiceModel.branch_id == branch.id, ServiceModel.branch_id.is_(None)))
        .all()
    )

    lengths = _lengths_by_service(db)
    return _branch_response(branch, [_summarise(s, lengths) for s in services])


@router.patch("/{branch_id}", response_model=BranchResponse)
def update_branch(
    branch_id: UUID,
    payload: BranchUpdateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> BranchResponse:
    branch = db.get(LocationModel, branch_id)
    if branch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(branch, field, value)

    db.commit()
    db.refresh(branch)

    services = (
        db.query(ServiceModel)
        .filter(or_(ServiceModel.branch_id == branch.id, ServiceModel.branch_id.is_(None)))
        .all()
    )

    lengths = _lengths_by_service(db)
    return _branch_response(branch, [_summarise(s, lengths) for s in services])


@router.delete("/{branch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_branch(
    branch_id: UUID,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> None:
    """Close a salon for good.

    Refused while anything meaningful still points at it - bookings (history),
    technicians homed there, its own services, or a discount - each with a
    message saying what to move first. Operational leftovers that mean nothing
    without the salon (locks, day assignments, rosters, allocation runs) are
    swept away with it."""
    branch = db.get(LocationModel, branch_id)
    if branch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")

    from app.bookings.infrastructure.models import BookingModel
    from app.discounts.infrastructure.models import DiscountModel
    from app.staff.infrastructure.models import StaffModel

    blockers = [
        (
            db.query(BookingModel.id).filter(BookingModel.branch_id == branch_id).first(),
            "Bookings have been made at this branch, so it cannot be removed",
        ),
        (
            db.query(StaffModel.id).filter(StaffModel.branch_id == branch_id).first(),
            "Technicians are homed at this branch - move them to another salon first",
        ),
        (
            db.query(ServiceModel.id).filter(ServiceModel.branch_id == branch_id).first(),
            "Services belong to this branch - move them to the whole chain or delete them first",
        ),
        (
            db.query(DiscountModel.id).filter(DiscountModel.branch_id == branch_id).first(),
            "A discount points at this branch - remove the discount first",
        ),
    ]
    for hit, message in blockers:
        if hit is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)

    from sqlalchemy import text as sql_text

    for table in ("slot_locks", "staff_day_assignments", "staff_rosters", "allocation_runs"):
        db.execute(
            sql_text(f"DELETE FROM {table} WHERE branch_id = :b"), {"b": str(branch_id)}
        )
    db.delete(branch)
    db.commit()
