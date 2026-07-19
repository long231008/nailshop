from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from redis import Redis
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.bookings.application.approve import SOFT_LOCK_TTL_SECONDS, approve_booking
from app.bookings.application.bill import get_bill
from app.bookings.application.complete import complete_booking_detail
from app.bookings.application.complete_manual import complete_booking_manually
from app.bookings.application.create import create_booking
from app.bookings.application.exceptions import (
    BookingDetailNotFoundError,
    BookingNotFoundError,
    DailyBookingLimitExceededError,
    InvalidBookingItemsError,
    InvalidBookingStateError,
    StaffConflictError,
)
from app.bookings.application.final_price import set_final_price
from app.bookings.application.no_show import mark_no_show
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel
from app.bookings.presentation.schemas import (
    BillResponse,
    BookingActionRequest,
    BookingApproveResponse,
    BookingCreateRequest,
    BookingDetailResponse,
    BookingResponse,
    BookingStatusResponse,
    FinalPriceRequest,
)
from app.shared.infrastructure.cache.redis_client import get_redis
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import CurrentUser, require_roles

router = APIRouter(prefix="/bookings", tags=["bookings"])


def _to_detail_response(detail: BookingDetailModel) -> BookingDetailResponse:
    return BookingDetailResponse(
        id=detail.id,
        service_id=detail.service_id,
        service_extension_id=detail.service_extension_id,
        staff_id=detail.staff_id,
        start_time=detail.start_time,
        end_time=detail.end_time,
        duration_min=detail.duration_min,
        price=float(detail.price),
        status=detail.status.value,
    )


def _to_booking_response(
    db: Session, booking: BookingModel, gift_message: str | None = None
) -> BookingResponse:
    details = (
        db.query(BookingDetailModel).filter(BookingDetailModel.booking_id == booking.id).all()
    )
    return BookingResponse(
        id=booking.id,
        customer_id=booking.customer_id,
        branch_id=booking.branch_id,
        booking_date=booking.booking_date,
        status=booking.status.value,
        total_price=float(booking.total_price) if booking.total_price is not None else None,
        final_price=float(booking.final_price) if booking.final_price is not None else None,
        deposit_amount=(
            float(booking.deposit_amount) if booking.deposit_amount is not None else None
        ),
        details=[_to_detail_response(d) for d in details],
        gift_message=gift_message,
    )


@router.post("", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
def create_booking_endpoint(
    payload: BookingCreateRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.CUSTOMER)),
) -> BookingResponse:
    try:
        booking, gift_message = create_booking(db, current_user.id, payload)
    except InvalidBookingItemsError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except StaffConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The requested staff member is not available at this time",
        )
    except DailyBookingLimitExceededError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Total booked duration for this day cannot exceed 2 hours",
        )

    return _to_booking_response(db, booking, gift_message)


@router.post("/{booking_id}/approve", response_model=BookingApproveResponse)
def approve_booking_endpoint(
    booking_id: UUID,
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> BookingApproveResponse:
    try:
        booking, deposit_link = approve_booking(db, redis_client, booking_id)
    except BookingNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    except InvalidBookingStateError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return BookingApproveResponse(
        id=booking.id,
        status=booking.status.value,
        deposit_amount=float(booking.deposit_amount),
        deposit_link=deposit_link,
        soft_lock_expires_in_seconds=SOFT_LOCK_TTL_SECONDS,
    )


@router.get("/{booking_id}/status", response_model=BookingStatusResponse)
def get_booking_status(
    booking_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.CUSTOMER)),
) -> BookingStatusResponse:
    booking = db.get(BookingModel, booking_id)
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    if current_user.role == UserRole.CUSTOMER and booking.customer_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this booking"
        )

    return BookingStatusResponse(
        id=booking.id,
        status=booking.status.value,
        total_price=float(booking.total_price) if booking.total_price is not None else None,
        final_price=float(booking.final_price) if booking.final_price is not None else None,
        deposit_amount=(
            float(booking.deposit_amount) if booking.deposit_amount is not None else None
        ),
    )


@router.post("/{booking_id}/complete", response_model=BookingResponse)
def complete_booking_endpoint(
    booking_id: UUID,
    payload: BookingActionRequest,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.STAFF)),
) -> BookingResponse:
    try:
        booking = complete_booking_detail(db, booking_id, payload.booking_detail_id)
    except BookingNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    except BookingDetailNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Booking detail not found"
        )

    return _to_booking_response(db, booking)


@router.post("/{booking_id}/no-show", response_model=BookingResponse)
def no_show_endpoint(
    booking_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.ADMIN)),
) -> BookingResponse:
    try:
        booking = mark_no_show(db, booking_id, current_user.id)
    except BookingNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    return _to_booking_response(db, booking)


@router.post("/{booking_id}/complete-manual", response_model=BookingResponse)
def complete_manual_endpoint(
    booking_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.ADMIN)),
) -> BookingResponse:
    try:
        booking = complete_booking_manually(db, booking_id, current_user.id)
    except BookingNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    return _to_booking_response(db, booking)


@router.post("/{booking_id}/final-price", response_model=BookingResponse)
def final_price_endpoint(
    booking_id: UUID,
    payload: FinalPriceRequest,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> BookingResponse:
    try:
        booking = set_final_price(db, booking_id, payload.final_price)
    except BookingNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    except InvalidBookingStateError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return _to_booking_response(db, booking)


@router.get("/{booking_id}/bill", response_model=BillResponse)
def bill_endpoint(
    booking_id: UUID,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> BillResponse:
    try:
        bill = get_bill(db, booking_id)
    except BookingNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    return BillResponse(**bill)
