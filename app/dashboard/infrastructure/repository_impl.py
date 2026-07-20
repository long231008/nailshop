from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.bookings.infrastructure.models import BookingModel, BookingStatus
from app.custom_designs.infrastructure.models import CustomDesignModel
from app.dashboard.domain.entities import BookingStatusCounts, DashboardSummary
from app.dashboard.domain.repository import DashboardRepository
from app.queue.infrastructure.models import QueueTicketModel, QueueTicketStatus, QueueTicketType
from app.staff.infrastructure.models import StaffModel, StaffStatus


class SqlAlchemyDashboardRepository(DashboardRepository):
    def __init__(self, db: Session):
        self._db = db

    def get_summary(self, branch_id: UUID | None, today: date) -> DashboardSummary:
        booking_query = self._db.query(BookingModel).filter(BookingModel.booking_date == today)
        if branch_id is not None:
            booking_query = booking_query.filter(BookingModel.branch_id == branch_id)

        counts = {status: 0 for status in BookingStatus}
        revenue = 0.0
        for booking in booking_query.all():
            counts[booking.status] += 1
            if booking.status == BookingStatus.COMPLETED:
                price = (
                    booking.final_price
                    if booking.final_price is not None
                    else booking.total_price
                )
                revenue += float(price or 0)

        queue_query = self._db.query(QueueTicketModel).filter(
            QueueTicketModel.ticket_type == QueueTicketType.WALKIN,
            QueueTicketModel.status == QueueTicketStatus.WAITING,
        )
        if branch_id is not None:
            queue_query = queue_query.filter(QueueTicketModel.branch_id == branch_id)
        queue_waiting_count = queue_query.count()

        pending_custom_designs = (
            self._db.query(CustomDesignModel)
            .filter(CustomDesignModel.estimated_price.is_(None))
            .count()
        )

        staff_query = self._db.query(StaffModel).filter(StaffModel.status == StaffStatus.ACTIVE)
        if branch_id is not None:
            staff_query = staff_query.filter(StaffModel.branch_id == branch_id)
        active_staff_count = staff_query.count()

        return DashboardSummary(
            date=today,
            bookings_today=BookingStatusCounts(
                pending=counts[BookingStatus.PENDING],
                approved=counts[BookingStatus.APPROVED],
                in_progress=counts[BookingStatus.IN_PROGRESS],
                completed=counts[BookingStatus.COMPLETED],
                cancelled=counts[BookingStatus.CANCELLED],
                no_show=counts[BookingStatus.NO_SHOW],
            ),
            revenue_today=revenue,
            queue_waiting_count=queue_waiting_count,
            pending_custom_designs=pending_custom_designs,
            active_staff_count=active_staff_count,
        )
