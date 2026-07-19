from uuid import UUID

from sqlalchemy.orm import Session

from app.services.infrastructure.models import ServiceExtensionModel, ServiceModel
from app.services.presentation.schemas import ServiceLengthCreateRequest


class ServiceNotFoundError(Exception):
    pass


def add_service_length(
    db: Session, service_id: UUID, payload: ServiceLengthCreateRequest
) -> ServiceExtensionModel:
    service = db.get(ServiceModel, service_id)
    if service is None:
        raise ServiceNotFoundError()

    extension = ServiceExtensionModel(
        service_id=service_id,
        name=payload.name,
        extra_price=payload.extra_price,
        extra_duration_min=payload.extra_duration_min,
    )
    db.add(extension)
    db.commit()
    db.refresh(extension)
    return extension
