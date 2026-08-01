from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import require_roles
from app.shifts.infrastructure.models import StaffRosterModel
from app.shifts.presentation.schemas import ShiftCreateRequest, ShiftResponse
from app.staff.infrastructure.models import StaffModel

router = APIRouter(prefix="/shifts", tags=["shifts"])


@router.get("", response_model=list[ShiftResponse])
def list_shifts(
    staff_id: UUID | None = None,
    branch_id: UUID | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> list[ShiftResponse]:
    query = db.query(StaffRosterModel)
    if staff_id is not None:
        query = query.filter(StaffRosterModel.staff_id == staff_id)
    if branch_id is not None:
        query = query.filter(StaffRosterModel.branch_id == branch_id)
    if from_ is not None:
        query = query.filter(StaffRosterModel.end_time >= from_)
    if to is not None:
        query = query.filter(StaffRosterModel.start_time <= to)

    shifts = query.order_by(StaffRosterModel.start_time).offset(offset).limit(limit).all()

    return [
        ShiftResponse(
            id=s.id,
            staff_id=s.staff_id,
            branch_id=s.branch_id,
            start_time=s.start_time,
            end_time=s.end_time,
        )
        for s in shifts
    ]


@router.post("", response_model=ShiftResponse, status_code=status.HTTP_201_CREATED)
def create_shift(
    payload: ShiftCreateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> ShiftResponse:
    staff = db.get(StaffModel, payload.staff_id)
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    # Techs belong to the chain; only reject when a non-floating tech is being
    # rostered away from their declared home branch.
    if not staff.floating and staff.branch_id is not None and staff.branch_id != payload.branch_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This staff member only works at their home branch",
        )

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


@router.delete("/{shift_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shift(
    shift_id: UUID,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> None:
    shift = db.get(StaffRosterModel, shift_id)
    if shift is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shift not found")

    db.delete(shift)
    db.commit()
