from uuid import UUID

from sqlalchemy.orm import Session

from app.custom_designs.application.exceptions import (
    CustomDesignAlreadyDecidedError,
    CustomDesignNotFoundError,
)
from app.custom_designs.infrastructure.models import CustomDesignModel, CustomDesignStatus


def set_estimated_price(
    db: Session, design_id: UUID, estimated_price: float
) -> CustomDesignModel:
    design = db.get(CustomDesignModel, design_id)
    if design is None:
        raise CustomDesignNotFoundError()
    if design.status in (CustomDesignStatus.ACCEPTED, CustomDesignStatus.REJECTED):
        raise CustomDesignAlreadyDecidedError()

    design.estimated_price = estimated_price
    design.status = CustomDesignStatus.PRICED
    db.commit()
    db.refresh(design)
    return design
