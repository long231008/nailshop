from dataclasses import dataclass
from datetime import date


@dataclass
class BookingStatusCounts:
    pending: int
    approved: int
    in_progress: int
    completed: int
    cancelled: int
    no_show: int


@dataclass
class DashboardSummary:
    date: date
    bookings_today: BookingStatusCounts
    pending_custom_designs: int
    active_staff_count: int


@dataclass
class RevenueBreakdown:
    today: float
    this_week: float
    this_month: float
    this_year: float


@dataclass
class RevenueSummary:
    deposit_revenue: RevenueBreakdown
    total_revenue: RevenueBreakdown
