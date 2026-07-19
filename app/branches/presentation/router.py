from collections import defaultdict

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.branches.presentation.schemas import (
    BranchCreateRequest,
    BranchResponse,
    BranchServiceSummary,
)
from app.branches.infrastructure.models import LocationModel
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import require_roles

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
