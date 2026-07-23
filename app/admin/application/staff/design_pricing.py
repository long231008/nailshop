from uuid import UUID

from sqlalchemy.orm import Session

from app.admin.application.staff.reserve import StaffNotFoundError
from app.staff.infrastructure.models import StaffModel


def set_design_pricing_permission(db: Session, staff_id: UUID, enabled: bool) -> StaffModel:
    staff = db.get(StaffModel, staff_id)
    if staff is None:
        raise StaffNotFoundError()

    staff.can_price_custom_designs = enabled
    db.commit()
    db.refresh(staff)
    return staff
