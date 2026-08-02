from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import CurrentUser, require_roles
from app.staff.application.schedule import get_staff_schedule
from app.staff.application.start_service import (
    BookingDetailNotFoundError,
    BookingNotStartableError,
    StaffBusyError,
    start_service,
)
from app.staff.infrastructure.models import StaffModel, StaffStatus
from app.staff.presentation.schemas import (
    StaffAppointment,
    StaffResponse,
    StaffScheduleResponse,
    StaffShift,
    StartServiceRequest,
)

router = APIRouter(prefix="/staff", tags=["staff"])


@router.get("", response_model=list[StaffResponse])
def list_staff(
    branch_id: UUID | None = None,
    status_filter: StaffStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> list[StaffResponse]:
    query = db.query(StaffModel)
    if branch_id is not None:
        query = query.filter(StaffModel.branch_id == branch_id)
    if status_filter is not None:
        query = query.filter(StaffModel.status == status_filter)

    staff_list = query.order_by(StaffModel.created_at).offset(offset).limit(limit).all()

    return [
        StaffResponse(
            id=s.id,
            user_id=s.user_id,
            branch_id=s.branch_id,
            display_name=s.display_name,
            status=s.status.value,
            can_lock_slots=s.can_lock_slots,
            created_at=s.created_at,
        )
        for s in staff_list
    ]


def _authorize_staff_access(db: Session, staff_id: UUID, current_user: CurrentUser) -> StaffModel:
    staff = db.get(StaffModel, staff_id)
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    if current_user.role == UserRole.STAFF and staff.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access your own staff record",
        )
    return staff


@router.get("/{staff_id}/schedule", response_model=StaffScheduleResponse)
def staff_schedule_endpoint(
    staff_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.STAFF)),
) -> StaffScheduleResponse:
    _authorize_staff_access(db, staff_id, current_user)
    data = get_staff_schedule(db, staff_id)

    return StaffScheduleResponse(
        staff_id=staff_id,
        shifts=[
            StaffShift(id=s.id, start_time=s.start_time, end_time=s.end_time)
            for s in data["shifts"]
        ],
        appointments=[
            StaffAppointment(
                booking_id=a.booking_id,
                booking_detail_id=a.id,
                service_id=a.service_id,
                start_time=a.start_time,
                end_time=a.end_time,
                status=a.status.value,
            )
            for a in data["appointments"]
        ],
    )


@router.post("/{staff_id}/start-service", response_model=StaffAppointment)
def start_service_endpoint(
    staff_id: UUID,
    payload: StartServiceRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.STAFF)),
) -> StaffAppointment:
    _authorize_staff_access(db, staff_id, current_user)

    try:
        detail = start_service(db, staff_id, payload.booking_detail_id)
    except BookingDetailNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking detail not found for this staff member",
        )
    except StaffBusyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This staff member is already serving another customer",
        )
    except BookingNotStartableError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail)

    return StaffAppointment(
        booking_id=detail.booking_id,
        booking_detail_id=detail.id,
        service_id=detail.service_id,
        start_time=detail.start_time,
        end_time=detail.end_time,
        status=detail.status.value,
    )
