from datetime import datetime
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.slot_locks.infrastructure.models import SlotLockModel


def locks_overlapping(
    db: Session, branch_id: UUID, start_time: datetime, end_time: datetime
) -> list[SlotLockModel]:
    return (
        db.query(SlotLockModel)
        .filter(
            SlotLockModel.branch_id == branch_id,
            SlotLockModel.start_time < end_time,
            SlotLockModel.end_time > start_time,
        )
        .all()
    )


def is_interval_locked(
    db: Session,
    branch_id: UUID,
    staff_id: UUID | None,
    start_time: datetime,
    end_time: datetime,
) -> bool:
    """A branch-wide lock blocks everyone; a staff lock blocks that member only."""
    staff_filter = SlotLockModel.staff_id.is_(None)
    if staff_id is not None:
        staff_filter = or_(staff_filter, SlotLockModel.staff_id == staff_id)

    return (
        db.query(SlotLockModel.id)
        .filter(
            SlotLockModel.branch_id == branch_id,
            staff_filter,
            SlotLockModel.start_time < end_time,
            SlotLockModel.end_time > start_time,
        )
        .first()
        is not None
    )
