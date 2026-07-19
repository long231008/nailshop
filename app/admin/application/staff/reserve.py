from uuid import UUID

from sqlalchemy.orm import Session

from app.staff.infrastructure.models import StaffModel, StaffStatus


class StaffNotFoundError(Exception):
    pass


def reserve_staff(db: Session, staff_id: UUID) -> StaffModel:
    staff = db.get(StaffModel, staff_id)
    if staff is None:
        raise StaffNotFoundError()

    staff.status = StaffStatus.RESERVED
    db.commit()
    db.refresh(staff)
    return staff
