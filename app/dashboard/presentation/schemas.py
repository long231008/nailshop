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
    revenue_today: float
    queue_waiting_count: int
    pending_custom_designs: int
    active_staff_count: int
