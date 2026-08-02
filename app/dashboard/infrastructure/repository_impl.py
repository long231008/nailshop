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

        # Custom designs are not tied to a branch, so this figure is global. When a
        # branch filter is active it is still shown - a request waiting to be priced
        # concerns every branch.
        pending_custom_designs = (
            self._db.query(CustomDesignModel)
            .filter(CustomDesignModel.estimated_price.is_(None))
            .count()
        )

        if branch_id is None:
            active_staff_count = (
                self._db.query(StaffModel).filter(StaffModel.status == StaffStatus.ACTIVE).count()
            )
        else:
            # Techs belong to the chain: today's headcount at a branch follows
            # the day roster (pins + Step A), falling back to home preference.
            from app.allocation.application.roster import expected_staff

            active_staff_count = len(expected_staff(self._db, branch_id, today))

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
        refund_types = [PaymentTransactionType.REFUND]

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

        def total_since(since: datetime) -> float:
            # Money returned to customers is not revenue: refunds recorded by the
            # payment webhook are subtracted so the totals reflect actual takings.
            return sum_since(total_types, since) - sum_since(refund_types, since)

        return RevenueSummary(
            deposit_revenue=RevenueBreakdown(
                today=sum_since(deposit_types, today_start),
                this_week=sum_since(deposit_types, week_start),
                this_month=sum_since(deposit_types, month_start),
                this_year=sum_since(deposit_types, year_start),
            ),
            total_revenue=RevenueBreakdown(
                today=total_since(today_start),
                this_week=total_since(week_start),
                this_month=total_since(month_start),
                this_year=total_since(year_start),
            ),
        )
