from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.bookings.infrastructure.models import BookingDetailModel
from app.branches.infrastructure.models import LocationModel
from app.discounts.infrastructure.models import DiscountModel
from app.services.application.lengths import ServiceNotFoundError, add_service_length
from app.services.infrastructure.models import ServiceExtensionModel, ServiceModel
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


@router.get("/{service_id}/lengths", response_model=list[ServiceLengthResponse])
def list_service_lengths(
    service_id: UUID,
    db: Session = Depends(get_db),
) -> list[ServiceLengthResponse]:
    """The length options a service offers - public, so the booking page can
    show them next to the service."""
    extensions = (
        db.query(ServiceExtensionModel)
        .filter(ServiceExtensionModel.service_id == service_id)
        .order_by(ServiceExtensionModel.extra_duration_min)
        .all()
    )
    return [
        ServiceLengthResponse(
            id=extension.id,
            service_id=extension.service_id,
            name=extension.name,
            extra_price=float(extension.extra_price),
            extra_duration_min=extension.extra_duration_min,
        )
        for extension in extensions
    ]


@router.delete("/lengths/{length_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service_length(
    length_id: UUID,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> None:
    """Take a length off the menu.

    Refused once a booking has chosen it: the nightly run reads the length back
    to work out how long that leg really needs, so detaching it would quietly
    shrink a long set to a short one on the day.
    """
    extension = db.get(ServiceExtensionModel, length_id)
    if extension is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Length not found")

    in_use = (
        db.query(BookingDetailModel.id)
        .filter(BookingDetailModel.service_extension_id == length_id)
        .first()
        is not None
    )
    if in_use:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bookings have been made with this length, so it cannot be removed",
        )

    db.delete(extension)
    db.commit()


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(
    service_id: UUID,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> None:
    """Take a service off the menu entirely - the cure for duplicated rows.

    Refused once any booking references it: past visits read their price and
    time from the service row, so deleting it would orphan history. Capability
    cells cascade away; length options go with the service."""
    service = db.get(ServiceModel, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")

    booked = (
        db.query(BookingDetailModel.id)
        .filter(BookingDetailModel.service_id == service_id)
        .first()
        is not None
    )
    if booked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bookings have been made with this service, so it cannot be removed",
        )

    discounted = (
        db.query(DiscountModel.id).filter(DiscountModel.service_id == service_id).first()
        is not None
    )
    if discounted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A discount points at this service - remove the discount first",
        )

    db.query(ServiceExtensionModel).filter(ServiceExtensionModel.service_id == service_id).delete(
        synchronize_session=False
    )
    db.delete(service)
    db.commit()
