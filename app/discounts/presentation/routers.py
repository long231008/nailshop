from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.discounts.presentation.schemas import DiscountCreateRequest, DiscountResponse
from app.discounts.infrastructure.models import DiscountModel
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import require_roles

router = APIRouter(prefix="/discounts", tags=["discounts"])


@router.post("", response_model=DiscountResponse, status_code=status.HTTP_201_CREATED)
def create_discount(
    payload: DiscountCreateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> DiscountResponse:
    discount = DiscountModel(
        name=payload.name,
        discount_type=payload.discount_type,
        value=payload.value,
        branch_id=payload.branch_id,
        service_id=payload.service_id,
        start_at=payload.start_at,
        end_at=payload.end_at,
    )
    db.add(discount)
    db.commit()
    db.refresh(discount)

    return DiscountResponse(
        id=discount.id,
        name=discount.name,
        discount_type=discount.discount_type,
        value=float(discount.value),
        branch_id=discount.branch_id,
        service_id=discount.service_id,
        start_at=discount.start_at,
        end_at=discount.end_at,
        is_active=discount.is_active,
    )
