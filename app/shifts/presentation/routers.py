from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.shifts.infrastructure.models import StaffRosterModel
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import require_roles
from app.shifts.presentation.schemas import ShiftCreateRequest, ShiftResponse

router = APIRouter(prefix="/shifts", tags=["shifts"])


@router.post("", response_model=ShiftResponse, status_code=status.HTTP_201_CREATED)
def create_shift(
    payload: ShiftCreateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> ShiftResponse:
    conflict = (
        db.query(StaffRosterModel)
        .filter(
            StaffRosterModel.staff_id == payload.staff_id,
            StaffRosterModel.start_time < payload.end_time,
            StaffRosterModel.end_time > payload.start_time,
        )
        .first()
    )
    if conflict is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This shift conflicts with an existing shift for this staff member",
        )

    shift = StaffRosterModel(
        staff_id=payload.staff_id,
        branch_id=payload.branch_id,
        start_time=payload.start_time,
        end_time=payload.end_time,
    )
    db.add(shift)
    db.commit()
    db.refresh(shift)

    return ShiftResponse(
        id=shift.id,
        staff_id=shift.staff_id,
        branch_id=shift.branch_id,
        start_time=shift.start_time,
        end_time=shift.end_time,
    )
