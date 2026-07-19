from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.admin.application.staff.reserve import StaffNotFoundError
from app.audit_log.infrastructure.models import AuditLogModel
from app.bookings.infrastructure.models import (
    BookingDetailModel,
    BookingDetailStatus,
    BookingModel,
    BookingStatus,
)
from app.shifts.infrastructure.models import StaffRosterModel
from app.staff.infrastructure.models import StaffModel, StaffStatus


def block_staff(db: Session, staff_id: UUID, actor_user_id: UUID) -> tuple[StaffModel, int]:
    staff = db.get(StaffModel, staff_id)
    if staff is None:
        raise StaffNotFoundError()

    staff.status = StaffStatus.BLOCKED

    now = datetime.now(timezone.utc)

    db.query(StaffRosterModel).filter(
        StaffRosterModel.staff_id == staff_id,
        StaffRosterModel.start_time > now,
    ).delete(synchronize_session=False)

    future_details = (
        db.query(BookingDetailModel)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .filter(
            BookingDetailModel.staff_id == staff_id,
            BookingDetailModel.status == BookingDetailStatus.PENDING,
            BookingDetailModel.start_time > now,
            BookingModel.status.notin_(
                [BookingStatus.CANCELLED, BookingStatus.NO_SHOW, BookingStatus.COMPLETED]
            ),
        )
        .all()
    )
    for detail in future_details:
        detail.staff_id = None

    db.add(
        AuditLogModel(
            actor_user_id=actor_user_id,
            action="staff.block",
            entity_type="staff",
            entity_id=staff.id,
            details={"unassigned_bookings": len(future_details)},
        )
    )

    db.commit()
    db.refresh(staff)
    return staff, len(future_details)
