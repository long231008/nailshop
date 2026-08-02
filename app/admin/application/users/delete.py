"""Deleting an account without destroying business records.

An account that never did anything is simply removed. An account woven into
bookings, payments, designs or audit history cannot be hard-deleted without
corrupting revenue figures and accountability - it is anonymized instead:
personal identifiers stripped, login blocked, history kept.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from app.audit_log.infrastructure.models import AuditLogModel
from app.auth.domain.value_object import UserStatus
from app.auth.infrastructure.models import UserModel
from app.bookings.infrastructure.models import BookingModel
from app.custom_designs.infrastructure.models import CustomDesignModel
from app.slot_locks.infrastructure.models import SlotLockModel
from app.staff.infrastructure.models import StaffModel


class UserNotFoundError(Exception):
    pass


def _has_history(db: Session, user_id: UUID, staff: StaffModel | None) -> bool:
    checks = [
        db.query(BookingModel.id).filter(BookingModel.customer_id == user_id),
        db.query(CustomDesignModel.id).filter(CustomDesignModel.customer_id == user_id),
        db.query(AuditLogModel.id).filter(AuditLogModel.actor_user_id == user_id),
        db.query(SlotLockModel.id).filter(SlotLockModel.created_by == user_id),
    ]
    if staff is not None:
        from app.bookings.infrastructure.models import BookingDetailModel
        from app.shifts.infrastructure.models import StaffRosterModel

        checks.append(
            db.query(BookingDetailModel.id).filter(BookingDetailModel.staff_id == staff.id)
        )
        checks.append(db.query(StaffRosterModel.id).filter(StaffRosterModel.staff_id == staff.id))
        checks.append(db.query(SlotLockModel.id).filter(SlotLockModel.staff_id == staff.id))

    return any(query.first() is not None for query in checks)


def delete_or_anonymize_user(db: Session, user_id: UUID, actor_user_id: UUID) -> str:
    """Returns "deleted" or "anonymized"."""
    user = db.get(UserModel, user_id)
    if user is None:
        raise UserNotFoundError()

    staff = db.query(StaffModel).filter(StaffModel.user_id == user_id).first()

    if _has_history(db, user_id, staff):
        user.phone_number = None
        user.email = None
        user.status = UserStatus.BLOCKED
        if staff is not None:
            from app.staff.infrastructure.models import StaffStatus

            staff.status = StaffStatus.BLOCKED
        mode = "anonymized"
    else:
        if staff is not None:
            db.delete(staff)
        db.delete(user)
        mode = "deleted"

    db.add(
        AuditLogModel(
            actor_user_id=actor_user_id,
            action=f"user.{mode}",
            entity_type="user",
            entity_id=user_id,
        )
    )
    db.commit()
    return mode
