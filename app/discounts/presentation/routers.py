from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.discounts.infrastructure.models import DiscountModel, DiscountType
from app.discounts.presentation.schemas import DiscountCreateRequest, DiscountResponse
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import require_roles

router = APIRouter(prefix="/discounts", tags=["discounts"])


def _to_response(discount: DiscountModel) -> DiscountResponse:
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

    return _to_response(discount)


@router.get("", response_model=list[DiscountResponse])
def list_discounts(
    is_active: bool | None = None,
    branch_id: UUID | None = None,
    discount_type: DiscountType | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> list[DiscountResponse]:
    query = db.query(DiscountModel)
    if is_active is not None:
        query = query.filter(DiscountModel.is_active == is_active)
    if branch_id is not None:
        query = query.filter(DiscountModel.branch_id == branch_id)
    if discount_type is not None:
        query = query.filter(DiscountModel.discount_type == discount_type)

    discounts = query.order_by(DiscountModel.created_at.desc()).offset(offset).limit(limit).all()

    return [_to_response(d) for d in discounts]
