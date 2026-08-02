from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from redis import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.infrastructure.models import UserModel
from app.auth.infrastructure.otp_repository_impl import RedisOtpRepository
from app.me.application.bookings import bookings_awaiting_deposit, list_my_bookings
from app.me.application.custom_designs import list_my_custom_designs
from app.me.application.email_change import (
    EmailAlreadyUsedError,
    EmailChangeTooManyAttemptsError,
    InvalidEmailCodeError,
    NoPendingEmailChangeError,
    confirm_email_change,
    request_email_change,
)
from app.me.presentation.schemas import (
    EmailChangeConfirmRequest,
    EmailChangeRequest,
    EmailChangeStarted,
    MyBookingSummary,
    MyCustomDesignSummary,
    MyProfileResponse,
    MyProfileUpdateRequest,
)
from app.notification.domain.sender import NotificationSender
from app.notification.infrastructure.senders import get_notification_sender
from app.shared.infrastructure.cache.redis_client import get_redis
from app.shared.infrastructure.config.settings import settings
from app.shared.infrastructure.database.session import get_db
from app.shared.infrastructure.rate_limit import limiter
from app.shared.presentation.dependencies import CurrentUser, get_current_user
from app.staff.infrastructure.models import StaffModel

router = APIRouter(prefix="/me", tags=["me"])


def _staff_identity(db: Session, user_id) -> tuple[UUID | None, str | None]:
    staff = db.query(StaffModel).filter(StaffModel.user_id == user_id).first()
    if staff is None:
        return None, None
    return staff.id, staff.display_name


@router.get("", response_model=MyProfileResponse)
def get_my_profile(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> MyProfileResponse:
    user = db.get(UserModel, current_user.id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    staff_id, staff_display_name = _staff_identity(db, user.id)
    return MyProfileResponse(
        id=user.id,
        phone_number=user.phone_number,
        email=user.email,
        first_name=user.first_name,
        surname=user.surname,
        role=user.role.value,
        status=user.status.value,
        created_at=user.created_at,
        staff_id=staff_id,
        staff_display_name=staff_display_name,
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

    # Email is not editable here: it is proof of identity for Google/Facebook
    # sign-in, so it goes through the verified change flow below.
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This phone number is already in use",
        )
    db.refresh(user)

    staff_id, staff_display_name = _staff_identity(db, user.id)
    return MyProfileResponse(
        id=user.id,
        phone_number=user.phone_number,
        email=user.email,
        first_name=user.first_name,
        surname=user.surname,
        role=user.role.value,
        status=user.status.value,
        created_at=user.created_at,
        staff_id=staff_id,
        staff_display_name=staff_display_name,
    )


@router.post("/email", response_model=EmailChangeStarted)
@limiter.limit("5/hour")
def request_email_change_endpoint(
    request: Request,
    payload: EmailChangeRequest,
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
    current_user: CurrentUser = Depends(get_current_user),
    notification_sender: NotificationSender = Depends(get_notification_sender),
) -> EmailChangeStarted:
    """Send a confirmation code to the new address; nothing changes until it comes back."""
    try:
        ttl = request_email_change(
            db,
            RedisOtpRepository(redis_client),
            notification_sender,
            current_user.id,
            payload.email,
        )
    except EmailAlreadyUsedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This email address is already in use",
        )

    return EmailChangeStarted(expires_in_seconds=ttl)


@router.post("/email/confirm", response_model=MyProfileResponse)
@limiter.limit("10/hour")
def confirm_email_change_endpoint(
    request: Request,
    payload: EmailChangeConfirmRequest,
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
    current_user: CurrentUser = Depends(get_current_user),
) -> MyProfileResponse:
    try:
        user = confirm_email_change(
            db, RedisOtpRepository(redis_client), current_user.id, payload.code
        )
    except NoPendingEmailChangeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No email change is pending, or it has expired",
        )
    except InvalidEmailCodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid confirmation code"
        )
    except EmailChangeTooManyAttemptsError:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many incorrect codes - please start the email change again",
        )
    except EmailAlreadyUsedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This email address is already in use",
        )

    return MyProfileResponse(
        id=user.id,
        phone_number=user.phone_number,
        email=user.email,
        first_name=user.first_name,
        surname=user.surname,
        role=user.role.value,
        status=user.status.value,
        created_at=user.created_at,
    )


@router.get("/bookings", response_model=list[MyBookingSummary])
def get_my_bookings(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[MyBookingSummary]:
    bookings = list_my_bookings(db, current_user.id, limit=limit, offset=offset)
    awaiting_deposit = bookings_awaiting_deposit(db, bookings)
    return [
        MyBookingSummary(
            id=b.id,
            branch_id=b.branch_id,
            booking_date=b.booking_date,
            status=b.status.value,
            total_price=float(b.total_price) if b.total_price is not None else None,
            deposit_amount=(float(b.deposit_amount) if b.deposit_amount is not None else None),
            deposit_link=(
                f"{settings.PAYMENT_CHECKOUT_BASE_URL}/{b.id}?amount={b.deposit_amount}"
                if b.id in awaiting_deposit
                else None
            ),
        )
        for b in bookings
    ]


@router.get("/custom-designs", response_model=list[MyCustomDesignSummary])
def get_my_custom_designs(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
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
