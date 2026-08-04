from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.audit_log.infrastructure.models import AuditLogModel
from app.auth.domain.value_object import UserRole
from app.leaves.infrastructure.models import StaffLeaveModel
from app.leaves.presentation.schemas import StaffLeaveCreateRequest, StaffLeaveResponse
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import CurrentUser, require_roles
from app.staff.infrastructure.models import StaffModel

router = APIRouter(prefix="/leaves", tags=["leaves"])


def _to_response(leave: StaffLeaveModel, staff_name: str | None) -> StaffLeaveResponse:
    return StaffLeaveResponse(
        id=leave.id,
        staff_id=leave.staff_id,
        staff_name=staff_name,
        start_time=leave.start_time,
        end_time=leave.end_time,
        reason=leave.reason,
        created_by=leave.created_by,
    )


@router.post("", response_model=StaffLeaveResponse, status_code=status.HTTP_201_CREATED)
def create_leave(
    payload: StaffLeaveCreateRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.ADMIN)),
) -> StaffLeaveResponse:
    """Book a technician off for a time range. The scheduler drops them from that
    window automatically the next time it runs (or reassign a day already
    allocated)."""
    staff = db.get(StaffModel, payload.staff_id)
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff member not found")

    leave = StaffLeaveModel(
        staff_id=payload.staff_id,
        start_time=payload.start_time,
        end_time=payload.end_time,
        reason=payload.reason,
        created_by=current_user.id,
    )
    db.add(leave)
    db.add(
        AuditLogModel(
            actor_user_id=current_user.id,
            action="staff_leave.created",
            entity_type="staff",
            entity_id=payload.staff_id,
            details={
                "start_time": payload.start_time.isoformat(),
                "end_time": payload.end_time.isoformat(),
                "reason": payload.reason,
            },
        )
    )
    db.commit()
    db.refresh(leave)
    return _to_response(leave, staff.display_name)


@router.get("", response_model=list[StaffLeaveResponse])
def list_leaves(
    staff_id: UUID | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.STAFF)),
) -> list[StaffLeaveResponse]:
    query = db.query(StaffLeaveModel, StaffModel.display_name).join(
        StaffModel, StaffLeaveModel.staff_id == StaffModel.id
    )
    if staff_id is not None:
        query = query.filter(StaffLeaveModel.staff_id == staff_id)
    if from_ is not None:
        query = query.filter(StaffLeaveModel.end_time >= from_)
    if to is not None:
        query = query.filter(StaffLeaveModel.start_time <= to)

    return [
        _to_response(leave, staff_name)
        for leave, staff_name in query.order_by(StaffLeaveModel.start_time).all()
    ]


@router.delete("/{leave_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_leave(
    leave_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.ADMIN)),
) -> None:
    leave = db.get(StaffLeaveModel, leave_id)
    if leave is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leave not found")

    db.add(
        AuditLogModel(
            actor_user_id=current_user.id,
            action="staff_leave.deleted",
            entity_type="staff",
            entity_id=leave.staff_id,
        )
    )
    db.delete(leave)
    db.commit()
