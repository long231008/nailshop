from collections import defaultdict
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.branches.infrastructure.models import LocationModel
from app.branches.presentation.schemas import (
    BranchCreateRequest,
    BranchResponse,
    BranchServiceSummary,
    BranchTechnicianSummary,
    BranchUpdateRequest,
)
from app.capability.application.matrix import is_available
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import require_roles
from app.staff.infrastructure.models import StaffModel, StaffStatus

router = APIRouter(prefix="/branches", tags=["branches"])


@router.post("", response_model=BranchResponse, status_code=status.HTTP_201_CREATED)
def create_branch(
    payload: BranchCreateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> BranchResponse:
    branch = LocationModel(
        name=payload.name, address=payload.address, phone_number=payload.phone_number
    )
    db.add(branch)
    db.commit()
    db.refresh(branch)

    return BranchResponse(
        id=branch.id,
        name=branch.name,
        address=branch.address,
        phone_number=branch.phone_number,
        services=[],
    )


@router.get("", response_model=list[BranchResponse])
def list_branches(db: Session = Depends(get_db)) -> list[BranchResponse]:
    branches = db.query(LocationModel).all()
    services = db.query(ServiceModel).all()

    services_by_branch: dict = defaultdict(list)
    global_services = []
    for service in services:
        summary = BranchServiceSummary(
            id=service.id,
            name=service.name,
            category=service.category,
            description=service.description,
            duration_min=service.duration_min,
            base_price=float(service.base_price),
        )
        if service.branch_id is None:
            global_services.append(summary)
        else:
            services_by_branch[service.branch_id].append(summary)

    return [
        BranchResponse(
            id=branch.id,
            name=branch.name,
            address=branch.address,
            phone_number=branch.phone_number,
            services=services_by_branch.get(branch.id, []) + global_services,
        )
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

    return BranchResponse(
        id=branch.id,
        name=branch.name,
        address=branch.address,
        phone_number=branch.phone_number,
        services=[
            BranchServiceSummary(
                id=s.id,
                name=s.name,
                category=s.category,
                description=s.description,
                duration_min=s.duration_min,
                base_price=float(s.base_price),
            )
            for s in services
        ],
    )


@router.get("/{branch_id}/technicians", response_model=list[BranchTechnicianSummary])
def list_branch_technicians(
    branch_id: UUID,
    on_date: date | None = Query(None, alias="date"),
    db: Session = Depends(get_db),
) -> list[BranchTechnicianSummary]:
    """Technicians a customer may wish for at this branch.

    Techs with no home branch float across the whole chain, so they are
    offered everywhere. With ?date= the list drops techs on their weekly day
    off, mirroring the check booking creation applies to a wish. Only names
    and ids leave the building.
    """
    branch = db.get(LocationModel, branch_id)
    if branch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")

    technicians = (
        db.query(StaffModel)
        .filter(
            StaffModel.status == StaffStatus.ACTIVE,
            or_(StaffModel.branch_id == branch.id, StaffModel.branch_id.is_(None)),
        )
        .order_by(StaffModel.display_name)
        .all()
    )
    if on_date is not None:
        technicians = [tech for tech in technicians if is_available(tech, on_date)]
    return [
        BranchTechnicianSummary(id=tech.id, display_name=tech.display_name) for tech in technicians
    ]


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

    return BranchResponse(
        id=branch.id,
        name=branch.name,
        address=branch.address,
        phone_number=branch.phone_number,
        services=[
            BranchServiceSummary(
                id=s.id,
                name=s.name,
                category=s.category,
                description=s.description,
                duration_min=s.duration_min,
                base_price=float(s.base_price),
            )
            for s in services
        ],
    )
