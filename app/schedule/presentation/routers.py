from datetime import date as date_type
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.availability.application.slot_finder import (
    BookingWindowClosedError,
    ServiceNotFoundError,
    find_available_slots,
)
from app.bookings.application.cancel import cancel_booking_by_salon
from app.bookings.application.exceptions import BookingNotFoundError, InvalidBookingStateError
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel
from app.notification.application.notify import notify_booking_cancelled_by_salon
from app.notification.domain.sender import NotificationSender
from app.notification.infrastructure.senders import get_notification_sender
from app.schedule.application.daily import get_daily_schedule, get_upcoming_pending
from app.schedule.application.day_sheet import get_day_sheets
from app.schedule.application.manage import (
    AppointmentConflictError,
    AppointmentError,
    AppointmentNotFoundError,
    add_appointment,
    reschedule_appointment,
)
from app.schedule.presentation.schemas import (
    AddAppointmentRequest,
    AppointmentMutationResponse,
    DailyAppointment,
    DailyScheduleResponse,
    DaySheet,
    DaySheetsResponse,
    DeskSlot,
    DeskSlotsResponse,
    PendingAppointment,
    RescheduleAppointmentRequest,
)
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import CurrentUser, require_roles
from app.staff.infrastructure.models import StaffModel

router = APIRouter(prefix="/schedule", tags=["schedule"])


def _mutation_response(db: Session, booking: BookingModel) -> AppointmentMutationResponse:
    first = (
        db.query(BookingDetailModel)
        .filter(BookingDetailModel.booking_id == booking.id)
        .order_by(BookingDetailModel.start_time)
        .first()
    )
    return AppointmentMutationResponse(
        booking_id=booking.id,
        status=booking.status.value,
        booking_date=booking.booking_date,
        staff_id=first.staff_id if first else None,
        start_time=first.start_time,
        end_time=first.end_time,
    )


@router.get("", response_model=DailyScheduleResponse)
def daily_schedule(
    date: date_type,
    branch_id: UUID | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.STAFF)),
) -> DailyScheduleResponse:
    """One day of the salon book. Admins and staff alike can look at any branch
    (or all of them at once). Appointments are deposit-secured; pending items
    still need a grant or their deposit. Revenue totals are admin-only."""
    appointments, pending = get_daily_schedule(db, date, branch_id)

    return DailyScheduleResponse(
        date=date,
        appointment_count=len(appointments),
        expected_value=(
            round(sum(a["price"] for a in appointments), 2)
            if current_user.role == UserRole.ADMIN
            else None
        ),
        appointments=[DailyAppointment(**a) for a in appointments],
        pending=[PendingAppointment(**p) for p in pending],
    )


@router.get("/slots", response_model=DeskSlotsResponse)
def desk_slots(
    branch_id: UUID,
    date: date_type,
    service_ids: str = Query(description="Comma-separated service ids of the visit, in order"),
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.STAFF)),
) -> DeskSlotsResponse:
    """Times the desk can still sell on this day, with the tidy ones marked.

    The same search the website runs, so a walk-in cannot be booked into a time
    the salon has no room for - but without the customer booking window, since
    the desk takes walk-ins for today and phone calls after tonight's close.
    """
    try:
        ids = [UUID(part) for part in service_ids.split(",") if part.strip()]
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="service_ids must be comma-separated UUIDs",
        )
    if not ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one service is required",
        )

    try:
        slots = find_available_slots(db, branch_id, ids, date, enforce_window=False)
    except ServiceNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    except BookingWindowClosedError:
        return DeskSlotsResponse(date=date, slots=[])

    return DeskSlotsResponse(date=date, slots=[DeskSlot(**slot) for slot in slots])


@router.get("/day-sheets", response_model=DaySheetsResponse)
def day_sheets(
    date: date_type,
    branch_id: UUID | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.STAFF)),
) -> DaySheetsResponse:
    """One sheet per technician for a day: their shop, and their own customers.

    Admins see everybody's. A member of staff sees their own and nobody else's,
    whatever they ask for - the filter is taken from the token, not the query.
    """
    only_staff_id = None
    if current_user.role != UserRole.ADMIN:
        member = db.query(StaffModel).filter(StaffModel.user_id == current_user.id).first()
        if member is None:
            return DaySheetsResponse(date=date, allocated=False, sheets=[])
        only_staff_id = member.id

    result = get_day_sheets(db, date, branch_id, only_staff_id)
    return DaySheetsResponse(
        date=result["date"],
        allocated=result["allocated"],
        sheets=[DaySheet(**sheet) for sheet in result["sheets"]],
    )


@router.get("/pending", response_model=list[PendingAppointment])
def upcoming_pending(
    branch_id: UUID | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.STAFF)),
) -> list[PendingAppointment]:
    """All future booking requests needing action, across every date."""
    pending = get_upcoming_pending(db, branch_id)[offset : offset + limit]
    return [PendingAppointment(**p) for p in pending]


@router.post(
    "/appointments",
    response_model=AppointmentMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_schedule_appointment(
    payload: AddAppointmentRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.STAFF)),
) -> AppointmentMutationResponse:
    """The desk enters a walk-in or phone booking straight onto the grid."""
    try:
        booking = add_appointment(
            db,
            current_user.id,
            payload.branch_id,
            payload.service_ids,
            payload.start_time,
            payload.staff_id,
            payload.customer_id,
            payload.customer_phone,
            payload.customer_name,
        )
    except AppointmentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except AppointmentConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That technician is not free at this time",
        )
    return _mutation_response(db, booking)


@router.patch("/appointments/{booking_id}", response_model=AppointmentMutationResponse)
def move_schedule_appointment(
    booking_id: UUID,
    payload: RescheduleAppointmentRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.STAFF)),
) -> AppointmentMutationResponse:
    """Move an appointment to a new start time."""
    try:
        booking = reschedule_appointment(db, current_user.id, booking_id, payload.new_start_time)
    except AppointmentNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    except AppointmentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except AppointmentConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That technician is not free at the new time",
        )
    return _mutation_response(db, booking)


@router.delete("/appointments/{booking_id}", response_model=AppointmentMutationResponse)
def delete_schedule_appointment(
    booking_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.STAFF)),
    notification_sender: NotificationSender = Depends(get_notification_sender),
) -> AppointmentMutationResponse:
    """Remove an appointment from the book (cancels it and tells the customer)."""
    try:
        booking = cancel_booking_by_salon(db, booking_id, current_user.id)
    except BookingNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    except InvalidBookingStateError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    notify_booking_cancelled_by_salon(notification_sender, db, booking.customer_id)
    return _mutation_response(db, booking)
