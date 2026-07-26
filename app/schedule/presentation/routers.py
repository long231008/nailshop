from datetime import date as date_type
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.schedule.application.daily import get_daily_schedule, get_upcoming_pending
from app.schedule.presentation.schemas import (
    DailyAppointment,
    DailyScheduleResponse,
    PendingAppointment,
)
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import require_roles

router = APIRouter(prefix="/schedule", tags=["schedule"])


@router.get("", response_model=DailyScheduleResponse)
def daily_schedule(
    date: date_type,
    branch_id: UUID | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.STAFF)),
) -> DailyScheduleResponse:
    """One day of the salon book. Admins and staff alike can look at any branch
    (or all of them at once). Appointments are deposit-secured; pending items
    still need a grant or their deposit."""
    appointments, pending = get_daily_schedule(db, date, branch_id)

    return DailyScheduleResponse(
        date=date,
        appointment_count=len(appointments),
        expected_value=round(sum(a["price"] for a in appointments), 2),
        appointments=[DailyAppointment(**a) for a in appointments],
        pending=[PendingAppointment(**p) for p in pending],
    )


@router.get("/pending", response_model=list[PendingAppointment])
def upcoming_pending(
    branch_id: UUID | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.STAFF)),
) -> list[PendingAppointment]:
    """All future booking requests needing action, across every date."""
    return [PendingAppointment(**p) for p in get_upcoming_pending(db, branch_id)]
