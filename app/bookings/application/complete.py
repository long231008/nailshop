from uuid import UUID

from sqlalchemy.orm import Session

from app.audit_log.infrastructure.models import AuditLogModel
from app.auth.domain.value_object import UserRole
from app.bookings.application.exceptions import (
    BookingDetailNotFoundError,
    BookingNotFoundError,
    InvalidBookingStateError,
    NotLegOwnerError,
)
from app.bookings.infrastructure.models import (
    BookingDetailModel,
    BookingDetailStatus,
    BookingModel,
    BookingStatus,
)
from app.staff.infrastructure.models import StaffModel

COMPLETABLE_STATUSES = (BookingStatus.APPROVED, BookingStatus.IN_PROGRESS)


def complete_booking_detail(
    db: Session,
    booking_id: UUID,
    booking_detail_id: UUID,
    actor_user_id: UUID,
    actor_role: UserRole,
) -> BookingModel:
    booking = db.get(BookingModel, booking_id)
    if booking is None:
        raise BookingNotFoundError()
    if booking.status not in COMPLETABLE_STATUSES:
        raise InvalidBookingStateError("Only approved or in-progress bookings can be completed")

    detail = db.get(BookingDetailModel, booking_detail_id)
    if detail is None or detail.booking_id != booking_id:
        raise BookingDetailNotFoundError()

    # A tech signs off their own work; only an admin closes someone else's leg.
    if actor_role != UserRole.ADMIN:
        staff = db.query(StaffModel).filter(StaffModel.user_id == actor_user_id).first()
        if staff is None or detail.staff_id != staff.id:
            raise NotLegOwnerError()

    detail.status = BookingDetailStatus.COMPLETED
    db.flush()

    all_details = (
        db.query(BookingDetailModel).filter(BookingDetailModel.booking_id == booking_id).all()
    )
    if all(d.status == BookingDetailStatus.COMPLETED for d in all_details):
        booking.status = BookingStatus.COMPLETED
        db.add(
            AuditLogModel(
                actor_user_id=actor_user_id,
                action="booking.completed",
                entity_type="booking",
                entity_id=booking.id,
            )
        )

    db.commit()
    db.refresh(booking)
    return booking
