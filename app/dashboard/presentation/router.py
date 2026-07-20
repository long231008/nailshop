from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.dashboard.application.get_revenue_summary import get_revenue_summary
from app.dashboard.application.get_summary import get_dashboard_summary
from app.dashboard.infrastructure.repository_impl import SqlAlchemyDashboardRepository
from app.dashboard.presentation.schemas import (
    BookingStatusCountsResponse,
    DashboardSummaryResponse,
    RevenueBreakdownResponse,
    RevenueSummaryResponse,
)
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import require_roles

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummaryResponse)
def dashboard_summary(
    branch_id: UUID | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> DashboardSummaryResponse:
    today = datetime.now(timezone.utc).date()
    summary = get_dashboard_summary(SqlAlchemyDashboardRepository(db), branch_id, today)

    return DashboardSummaryResponse(
        date=summary.date,
        bookings_today=BookingStatusCountsResponse(
            pending=summary.bookings_today.pending,
            approved=summary.bookings_today.approved,
            in_progress=summary.bookings_today.in_progress,
            completed=summary.bookings_today.completed,
            cancelled=summary.bookings_today.cancelled,
            no_show=summary.bookings_today.no_show,
        ),
        queue_waiting_count=summary.queue_waiting_count,
        pending_custom_designs=summary.pending_custom_designs,
        active_staff_count=summary.active_staff_count,
    )


@router.get("/revenue", response_model=RevenueSummaryResponse)
def dashboard_revenue(
    branch_id: UUID | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> RevenueSummaryResponse:
    now = datetime.now(timezone.utc)
    summary = get_revenue_summary(SqlAlchemyDashboardRepository(db), branch_id, now)

    return RevenueSummaryResponse(
        deposit_revenue=RevenueBreakdownResponse(
            today=summary.deposit_revenue.today,
            this_week=summary.deposit_revenue.this_week,
            this_month=summary.deposit_revenue.this_month,
            this_year=summary.deposit_revenue.this_year,
        ),
        total_revenue=RevenueBreakdownResponse(
            today=summary.total_revenue.today,
            this_week=summary.total_revenue.this_week,
            this_month=summary.total_revenue.this_month,
            this_year=summary.total_revenue.this_year,
        ),
    )
