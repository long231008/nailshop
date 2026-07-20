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
    revenue_today: float
    queue_waiting_count: int
    pending_custom_designs: int
    active_staff_count: int
