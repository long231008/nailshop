from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.bookings.infrastructure.models import BookingModel, BookingStatus
from app.custom_designs.infrastructure.models import CustomDesignModel
from app.dashboard.domain.entities import (
    BookingStatusCounts,
    DashboardSummary,
    RevenueBreakdown,
    RevenueSummary,
)
from app.dashboard.domain.repository import DashboardRepository
from app.queue.infrastructure.models import QueueTicketModel, QueueTicketStatus, QueueTicketType
from app.shared.infrastructure.clock import (
    start_of_day_utc,
    start_of_month_utc,
    start_of_week_utc,
    start_of_year_utc,
)
from app.staff.infrastructure.models import StaffModel, StaffStatus
from app.webhooks.infrastructure.models import (
    PaymentTransactionModel,
    PaymentTransactionStatus,
    PaymentTransactionType,
)


class SqlAlchemyDashboardRepository(DashboardRepository):
    def __init__(self, db: Session):
        self._db = db

    def get_summary(self, branch_id: UUID | None, today: date) -> DashboardSummary:
        booking_query = self._db.query(BookingModel).filter(BookingModel.booking_date == today)
        if branch_id is not None:
            booking_query = booking_query.filter(BookingModel.branch_id == branch_id)

        counts = {status: 0 for status in BookingStatus}
        for booking in booking_query.all():
            counts[booking.status] += 1

        queue_query = self._db.query(QueueTicketModel).filter(
            QueueTicketModel.ticket_type == QueueTicketType.WALKIN,
            QueueTicketModel.status == QueueTicketStatus.WAITING,
        )
        if branch_id is not None:
            queue_query = queue_query.filter(QueueTicketModel.branch_id == branch_id)
        queue_waiting_count = queue_query.count()

        # Custom designs are not tied to a branch, so this figure is global. When a
        # branch filter is active it is still shown - a request waiting to be priced
        # concerns every branch.
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
            queue_waiting_count=queue_waiting_count,
            pending_custom_designs=pending_custom_designs,
            active_staff_count=active_staff_count,
        )

    def get_revenue_summary(self, branch_id: UUID | None, now: datetime) -> RevenueSummary:
        today_start = start_of_day_utc(now)
        week_start = start_of_week_utc(now)
        month_start = start_of_month_utc(now)
        year_start = start_of_year_utc(now)

        deposit_types = [PaymentTransactionType.DEPOSIT]
        total_types = [PaymentTransactionType.DEPOSIT, PaymentTransactionType.FINAL_PAYMENT]

        def sum_since(transaction_types: list, since: datetime) -> float:
            query = (
                self._db.query(func.coalesce(func.sum(PaymentTransactionModel.amount), 0))
                .join(BookingModel, PaymentTransactionModel.booking_id == BookingModel.id)
                .filter(
                    PaymentTransactionModel.transaction_type.in_(transaction_types),
                    PaymentTransactionModel.status == PaymentTransactionStatus.SUCCESS,
                    PaymentTransactionModel.created_at >= since,
                )
            )
            if branch_id is not None:
                query = query.filter(BookingModel.branch_id == branch_id)
            return float(query.scalar() or 0)

        return RevenueSummary(
            deposit_revenue=RevenueBreakdown(
                today=sum_since(deposit_types, today_start),
                this_week=sum_since(deposit_types, week_start),
                this_month=sum_since(deposit_types, month_start),
                this_year=sum_since(deposit_types, year_start),
            ),
            total_revenue=RevenueBreakdown(
                today=sum_since(total_types, today_start),
                this_week=sum_since(total_types, week_start),
                this_month=sum_since(total_types, month_start),
                this_year=sum_since(total_types, year_start),
            ),
        )
