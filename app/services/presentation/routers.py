from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.branches.infrastructure.models import LocationModel
from app.services.application.lengths import ServiceNotFoundError, add_service_length
from app.services.infrastructure.models import ServiceModel
from app.services.presentation.schemas import (
    ServiceCreateRequest,
    ServiceLengthCreateRequest,
    ServiceLengthResponse,
    ServiceResponse,
    ServiceUpdateRequest,
)
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import require_roles

router = APIRouter(prefix="/services", tags=["services"])


@router.post("", response_model=ServiceResponse, status_code=status.HTTP_201_CREATED)
def create_service(
    payload: ServiceCreateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> ServiceResponse:
    if payload.branch_id is not None and db.get(LocationModel, payload.branch_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")

    service = ServiceModel(
        branch_id=payload.branch_id,
        name=payload.name,
        category=payload.category,
        description=payload.description,
        duration_min=payload.duration_min,
        base_price=payload.base_price,
    )
    db.add(service)
    db.commit()
    db.refresh(service)

    return ServiceResponse(
        id=service.id,
        branch_id=service.branch_id,
        name=service.name,
        category=service.category,
        description=service.description,
        duration_min=service.duration_min,
        base_price=float(service.base_price),
    )


@router.get("", response_model=list[ServiceResponse])
def list_services(
    branch_id: UUID | None = None,
    category: str | None = None,
    db: Session = Depends(get_db),
) -> list[ServiceResponse]:
    query = db.query(ServiceModel)
    if branch_id is not None:
        query = query.filter(ServiceModel.branch_id == branch_id)
    if category is not None:
        query = query.filter(ServiceModel.category == category)

    return [
        ServiceResponse(
            id=service.id,
            branch_id=service.branch_id,
            name=service.name,
            category=service.category,
            description=service.description,
            duration_min=service.duration_min,
            base_price=float(service.base_price),
        )
        for service in query.all()
    ]


@router.patch("/{service_id}", response_model=ServiceResponse)
def update_service(
    service_id: UUID,
    payload: ServiceUpdateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> ServiceResponse:
    service = db.get(ServiceModel, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(service, field, value)

    db.commit()
    db.refresh(service)

    return ServiceResponse(
        id=service.id,
        branch_id=service.branch_id,
        name=service.name,
        category=service.category,
        description=service.description,
        duration_min=service.duration_min,
        base_price=float(service.base_price),
    )


@router.post(
    "/{service_id}/lengths",
    response_model=ServiceLengthResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_service_length(
    service_id: UUID,
    payload: ServiceLengthCreateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> ServiceLengthResponse:
    try:
        extension = add_service_length(db, service_id, payload)
    except ServiceNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")

    return ServiceLengthResponse(
        id=extension.id,
        service_id=extension.service_id,
        name=extension.name,
        extra_price=float(extension.extra_price),
        extra_duration_min=extension.extra_duration_min,
    )
