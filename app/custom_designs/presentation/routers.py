from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.bookings.application.exceptions import (
    DailyBookingLimitExceededError,
    InvalidBookingItemsError,
    StaffConflictError,
)
from app.custom_designs.application.decide import accept_custom_design, reject_custom_design
from app.custom_designs.application.exceptions import (
    CustomDesignAlreadyDecidedError,
    CustomDesignNotFoundError,
    CustomDesignNotOwnedError,
    CustomDesignNotPricedError,
)
from app.custom_designs.application.price import set_estimated_price
from app.custom_designs.infrastructure.models import CustomDesignModel, CustomDesignStatus
from app.custom_designs.infrastructure.storage import CloudinaryStorage, get_design_storage
from app.custom_designs.presentation.schemas import (
    AcceptCustomDesignRequest,
    AcceptCustomDesignResponse,
    CustomDesignPriceRequest,
    CustomDesignResponse,
)
from app.notification.application.notify import notify_design_priced
from app.notification.domain.sender import NotificationSender
from app.notification.infrastructure.senders import get_notification_sender
from app.shared.infrastructure.database.session import get_db
from app.shared.infrastructure.rate_limit import limiter
from app.shared.presentation.dependencies import CurrentUser, require_roles
from app.staff.infrastructure.models import StaffModel

router = APIRouter(prefix="/custom-designs", tags=["custom-designs"])


def _to_response(design: CustomDesignModel) -> CustomDesignResponse:
    return CustomDesignResponse(
        id=design.id,
        customer_id=design.customer_id,
        image_url=design.image_url,
        description=design.description,
        estimated_price=(
            float(design.estimated_price) if design.estimated_price is not None else None
        ),
        status=design.status.value,
    )


def _authorize_price_setter(db: Session, current_user: CurrentUser) -> None:
    if current_user.role == UserRole.ADMIN:
        return
    staff = db.query(StaffModel).filter(StaffModel.user_id == current_user.id).first()
    if staff is None or not staff.can_price_custom_designs:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to price custom designs",
        )


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/heic"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
# A design request is cheap for us and free for the sender, so cap the flow.
MAX_OPEN_REQUESTS_PER_CUSTOMER = 10


@router.post("", response_model=CustomDesignResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/hour")
def create_custom_design(
    request: Request,
    file: UploadFile | None = File(default=None),
    description: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.CUSTOMER)),
    storage: CloudinaryStorage = Depends(get_design_storage),
) -> CustomDesignResponse:
    # A request needs at least one of the two: a reference photo or words.
    if file is None and not (description and description.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please attach a photo or describe the design you want",
        )

    open_requests = (
        db.query(CustomDesignModel)
        .filter(
            CustomDesignModel.customer_id == current_user.id,
            CustomDesignModel.status.in_([CustomDesignStatus.PENDING, CustomDesignStatus.PRICED]),
        )
        .count()
    )
    if open_requests >= MAX_OPEN_REQUESTS_PER_CUSTOMER:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="You already have several design requests open. Please use or decline them first.",
        )

    image_url = None
    if file is not None:
        # The declared type is client-controlled, so this is a first gate only;
        # Cloudinary re-validates the actual bytes because we upload with
        # resource_type="image".
        if file.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only image uploads (JPEG, PNG, WebP, GIF, HEIC) are accepted",
            )
        if file.size is not None and file.size > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Images must be under {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
            )
        image_url = storage.save(file)

    design = CustomDesignModel(
        customer_id=current_user.id, image_url=image_url, description=description
    )
    db.add(design)
    db.commit()
    db.refresh(design)
    return _to_response(design)


@router.get("", response_model=list[CustomDesignResponse])
def list_custom_designs(
    priced: bool | None = None,
    customer_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> list[CustomDesignResponse]:
    query = db.query(CustomDesignModel)
    if priced is True:
        query = query.filter(CustomDesignModel.estimated_price.isnot(None))
    elif priced is False:
        query = query.filter(CustomDesignModel.estimated_price.is_(None))
    if customer_id is not None:
        query = query.filter(CustomDesignModel.customer_id == customer_id)

    designs = query.order_by(CustomDesignModel.created_at.desc()).offset(offset).limit(limit).all()

    return [_to_response(d) for d in designs]


@router.post("/{design_id}/price", response_model=CustomDesignResponse)
def set_custom_design_price(
    design_id: UUID,
    payload: CustomDesignPriceRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.STAFF)),
    notification_sender: NotificationSender = Depends(get_notification_sender),
) -> CustomDesignResponse:
    _authorize_price_setter(db, current_user)

    try:
        design = set_estimated_price(db, design_id, payload.estimated_price)
    except CustomDesignNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom design not found")
    except CustomDesignAlreadyDecidedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This design has already been accepted or rejected",
        )

    # The quote travels to the customer on the channel they signed up with.
    notify_design_priced(notification_sender, db, design.customer_id, float(design.estimated_price))

    return _to_response(design)


@router.post("/{design_id}/accept", response_model=AcceptCustomDesignResponse)
def accept_custom_design_endpoint(
    design_id: UUID,
    payload: AcceptCustomDesignRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.CUSTOMER)),
) -> AcceptCustomDesignResponse:
    try:
        booking, design, gift_message = accept_custom_design(
            db,
            current_user.id,
            design_id,
            payload.branch_id,
            payload.service_id,
            payload.staff_id,
            payload.start_time,
        )
    except CustomDesignNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom design not found")
    except CustomDesignNotOwnedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this design",
        )
    except CustomDesignNotPricedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This design must be priced before it can be accepted",
        )
    except InvalidBookingItemsError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except (StaffConflictError, DailyBookingLimitExceededError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That time is no longer available",
        )

    return AcceptCustomDesignResponse(
        design_id=design.id,
        booking_id=booking.id,
        booking_status=booking.status.value,
        price=float(design.estimated_price),
        gift_message=gift_message,
    )


@router.post("/{design_id}/reject", response_model=CustomDesignResponse)
def reject_custom_design_endpoint(
    design_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.CUSTOMER)),
) -> CustomDesignResponse:
    try:
        design = reject_custom_design(db, current_user.id, design_id)
    except CustomDesignNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom design not found")
    except CustomDesignNotOwnedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this design",
        )
    except CustomDesignAlreadyDecidedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This design has already been accepted or rejected",
        )

    return _to_response(design)
