from uuid import UUID

from sqlalchemy.orm import Session

from app.custom_designs.infrastructure.models import CustomDesignModel


def list_my_custom_designs(db: Session, customer_id: UUID) -> list[CustomDesignModel]:
    return (
        db.query(CustomDesignModel)
        .filter(CustomDesignModel.customer_id == customer_id)
        .order_by(CustomDesignModel.created_at.desc())
        .all()
    )
