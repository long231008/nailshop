from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.custom_designs.application.price import CustomDesignNotFoundError, set_estimated_price
from app.custom_designs.infrastructure.models import CustomDesignModel
from app.custom_designs.infrastructure.storage import LocalFileStorage
from app.custom_designs.presentation.schemas import (
    CustomDesignPriceRequest,
    CustomDesignResponse,
)
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import CurrentUser, require_roles

router = APIRouter(prefix="/custom-designs", tags=["custom-designs"])
storage = LocalFileStorage()


def _to_response(design: CustomDesignModel) -> CustomDesignResponse:
    return CustomDesignResponse(
        id=design.id,
        customer_id=design.customer_id,
        image_url=design.image_url,
        description=design.description,
        estimated_price=(
            float(design.estimated_price) if design.estimated_price is not None else None
        ),
    )


@router.post("", response_model=CustomDesignResponse, status_code=status.HTTP_201_CREATED)
def create_custom_design(
    file: UploadFile = File(...),
    description: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.CUSTOMER)),
) -> CustomDesignResponse:
    image_url = storage.save(file)
    design = CustomDesignModel(
        customer_id=current_user.id, image_url=image_url, description=description
    )
    db.add(design)
    db.commit()
    db.refresh(design)
    return _to_response(design)


@router.post("/{design_id}/price", response_model=CustomDesignResponse)
def set_custom_design_price(
    design_id: UUID,
    payload: CustomDesignPriceRequest,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> CustomDesignResponse:
    try:
        design = set_estimated_price(db, design_id, payload.estimated_price)
    except CustomDesignNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Custom design not found"
        )

    return _to_response(design)
