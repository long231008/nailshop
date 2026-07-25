from uuid import UUID

from sqlalchemy.orm import Session

from app.admin.application.staff.reserve import StaffNotFoundError
from app.audit_log.infrastructure.models import AuditLogModel
from app.staff.infrastructure.models import StaffModel, StaffStatus


def activate_staff(db: Session, staff_id: UUID, actor_user_id: UUID) -> StaffModel:
    staff = db.get(StaffModel, staff_id)
    if staff is None:
        raise StaffNotFoundError()

    previous = staff.status
    staff.status = StaffStatus.ACTIVE
    db.add(
        AuditLogModel(
            actor_user_id=actor_user_id,
            action="staff.activated",
            entity_type="staff",
            entity_id=staff.id,
            details={"previous_status": previous.value},
        )
    )
    db.commit()
    db.refresh(staff)
    return staff
