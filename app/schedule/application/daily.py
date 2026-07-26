from datetime import date as date_type
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.infrastructure.models import UserModel
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import day_bounds_utc
from app.staff.infrastructure.models import StaffModel

# "Confirmed" from the salon floor's point of view: money is promised or the
# customer is already in the chair. Pending/cancelled/no-show never show up.
CONFIRMED_STATUSES = [
    BookingStatus.APPROVED,
    BookingStatus.IN_PROGRESS,
    BookingStatus.COMPLETED,
]


def get_daily_schedule(db: Session, target_date: date_type, branch_id: UUID | None) -> list[dict]:
    day_start, day_end = day_bounds_utc(target_date)

    query = (
        db.query(BookingDetailModel, BookingModel, ServiceModel.name, StaffModel, UserModel)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .join(ServiceModel, BookingDetailModel.service_id == ServiceModel.id)
        .outerjoin(StaffModel, BookingDetailModel.staff_id == StaffModel.id)
        .join(UserModel, BookingModel.customer_id == UserModel.id)
        .filter(
            BookingModel.status.in_(CONFIRMED_STATUSES),
            BookingDetailModel.start_time >= day_start,
            BookingDetailModel.start_time < day_end,
        )
    )
    if branch_id is not None:
        query = query.filter(BookingModel.branch_id == branch_id)

    rows = query.order_by(BookingDetailModel.start_time).all()

    appointments = []
    for detail, booking, service_name, staff, customer in rows:
        customer_name = " ".join(part for part in (customer.first_name, customer.surname) if part)
        appointments.append(
            {
                "booking_id": booking.id,
                "branch_id": booking.branch_id,
                "start_time": detail.start_time,
                "end_time": detail.end_time,
                "service_name": service_name,
                "staff_name": staff.display_name if staff else None,
                "customer_name": customer_name or None,
                "customer_phone": customer.phone_number,
                "price": float(detail.price),
                "status": booking.status.value,
            }
        )
    return appointments
