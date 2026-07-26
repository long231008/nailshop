from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.audit_log.infrastructure.models import AuditLogModel
from app.auth.domain.value_object import UserRole
from app.branches.infrastructure.models import LocationModel
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import CurrentUser, require_roles
from app.slot_locks.infrastructure.models import SlotLockModel
from app.slot_locks.presentation.schemas import SlotLockCreateRequest, SlotLockResponse
from app.staff.infrastructure.models import StaffModel

router = APIRouter(prefix="/slot-locks", tags=["slot-locks"])


def _to_response(lock: SlotLockModel) -> SlotLockResponse:
    return SlotLockResponse(
        id=lock.id,
        branch_id=lock.branch_id,
        staff_id=lock.staff_id,
        start_time=lock.start_time,
        end_time=lock.end_time,
        reason=lock.reason,
        created_by=lock.created_by,
    )


def _authorize_lock_manager(db: Session, current_user: CurrentUser, branch_id: UUID) -> None:
    """Admins manage locks everywhere; staff need the delegated permission and can
    only lock time at their own branch."""
    if current_user.role == UserRole.ADMIN:
        return
    staff = db.query(StaffModel).filter(StaffModel.user_id == current_user.id).first()
    if staff is None or not staff.can_lock_slots:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to lock time slots",
        )
    if staff.branch_id != branch_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only lock time slots at your own branch",
        )


@router.post("", response_model=SlotLockResponse, status_code=status.HTTP_201_CREATED)
def create_slot_lock(
    payload: SlotLockCreateRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.STAFF)),
) -> SlotLockResponse:
    _authorize_lock_manager(db, current_user, payload.branch_id)

    if db.get(LocationModel, payload.branch_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")
    if payload.staff_id is not None:
        staff = db.get(StaffModel, payload.staff_id)
        if staff is None or staff.branch_id != payload.branch_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Staff member not found at this branch",
            )

    lock = SlotLockModel(
        branch_id=payload.branch_id,
        staff_id=payload.staff_id,
        start_time=payload.start_time,
        end_time=payload.end_time,
        reason=payload.reason,
        created_by=current_user.id,
    )
    db.add(lock)
    db.add(
        AuditLogModel(
            actor_user_id=current_user.id,
            action="slot_lock.created",
            entity_type="slot_lock",
            entity_id=lock.id,
            details={
                "branch_id": str(payload.branch_id),
                "staff_id": str(payload.staff_id) if payload.staff_id else None,
                "start_time": payload.start_time.isoformat(),
                "end_time": payload.end_time.isoformat(),
                "reason": payload.reason,
            },
        )
    )
    db.commit()
    db.refresh(lock)
    return _to_response(lock)


@router.get("", response_model=list[SlotLockResponse])
def list_slot_locks(
    branch_id: UUID,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.STAFF)),
) -> list[SlotLockResponse]:
    _authorize_lock_manager(db, current_user, branch_id)

    query = db.query(SlotLockModel).filter(SlotLockModel.branch_id == branch_id)
    if from_ is not None:
        query = query.filter(SlotLockModel.end_time >= from_)
    if to is not None:
        query = query.filter(SlotLockModel.start_time <= to)

    return [_to_response(lock) for lock in query.order_by(SlotLockModel.start_time).all()]


@router.delete("/{lock_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_slot_lock(
    lock_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.STAFF)),
) -> None:
    lock = db.get(SlotLockModel, lock_id)
    if lock is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slot lock not found")

    _authorize_lock_manager(db, current_user, lock.branch_id)

    db.add(
        AuditLogModel(
            actor_user_id=current_user.id,
            action="slot_lock.deleted",
            entity_type="slot_lock",
            entity_id=lock.id,
        )
    )
    db.delete(lock)
    db.commit()
