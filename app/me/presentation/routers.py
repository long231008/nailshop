from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.auth.infrastructure.models import UserModel
from app.me.application.bookings import list_my_bookings
from app.me.application.custom_designs import list_my_custom_designs
from app.me.presentation.schemas import (
    MyBookingSummary,
    MyCustomDesignSummary,
    MyProfileResponse,
    MyProfileUpdateRequest,
)
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import CurrentUser, get_current_user, require_roles

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", response_model=MyProfileResponse)
def get_my_profile(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> MyProfileResponse:
    user = db.get(UserModel, current_user.id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return MyProfileResponse(
        id=user.id,
        phone_number=user.phone_number,
        email=user.email,
        role=user.role.value,
        status=user.status.value,
        created_at=user.created_at,
    )


@router.patch("", response_model=MyProfileResponse)
def update_my_profile(
    payload: MyProfileUpdateRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> MyProfileResponse:
    user = db.get(UserModel, current_user.id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This phone number or email is already in use",
        )
    db.refresh(user)

    return MyProfileResponse(
        id=user.id,
        phone_number=user.phone_number,
        email=user.email,
        role=user.role.value,
        status=user.status.value,
        created_at=user.created_at,
    )


@router.get("/bookings", response_model=list[MyBookingSummary])
def get_my_bookings(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.CUSTOMER)),
) -> list[MyBookingSummary]:
    bookings = list_my_bookings(db, current_user.id)
    return [
        MyBookingSummary(
            id=b.id,
            branch_id=b.branch_id,
            booking_date=b.booking_date,
            status=b.status.value,
            total_price=float(b.total_price) if b.total_price is not None else None,
        )
        for b in bookings
    ]


@router.get("/custom-designs", response_model=list[MyCustomDesignSummary])
def get_my_custom_designs(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.CUSTOMER)),
) -> list[MyCustomDesignSummary]:
    designs = list_my_custom_designs(db, current_user.id)
    return [
        MyCustomDesignSummary(
            id=d.id,
            image_url=d.image_url,
            description=d.description,
            estimated_price=float(d.estimated_price) if d.estimated_price is not None else None,
            status=d.status.value,
        )
        for d in designs
    ]
