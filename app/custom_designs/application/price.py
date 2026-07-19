from uuid import UUID

from sqlalchemy.orm import Session

from app.custom_designs.infrastructure.models import CustomDesignModel


class CustomDesignNotFoundError(Exception):
    pass


def set_estimated_price(
    db: Session, design_id: UUID, estimated_price: float
) -> CustomDesignModel:
    design = db.get(CustomDesignModel, design_id)
    if design is None:
        raise CustomDesignNotFoundError()

    design.estimated_price = estimated_price
    db.commit()
    db.refresh(design)
    return design
