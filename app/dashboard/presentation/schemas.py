from datetime import date

from pydantic import BaseModel


class BookingStatusCountsResponse(BaseModel):
    pending: int
    approved: int
    in_progress: int
    completed: int
    cancelled: int
    no_show: int


class DashboardSummaryResponse(BaseModel):
    date: date
    bookings_today: BookingStatusCountsResponse
    pending_custom_designs: int
    active_staff_count: int


class RevenueBreakdownResponse(BaseModel):
    today: float
    this_week: float
    this_month: float
    this_year: float


class RevenueSummaryResponse(BaseModel):
    deposit_revenue: RevenueBreakdownResponse
    total_revenue: RevenueBreakdownResponse
