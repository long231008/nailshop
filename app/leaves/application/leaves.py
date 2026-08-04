"""Staff leave queries the scheduler and capacity ledger lean on.

A leave is a plain (staff, start, end) window. Everything that decides "can this
technician work now" - the capacity ledger, the nightly allocator, manual
reassign - asks here, so leave is honoured in exactly one shape everywhere.
"""

from datetime import date as date_type
from uuid import UUID

from sqlalchemy.orm import Session

from app.leaves.infrastructure.models import StaffLeaveModel
from app.shared.infrastructure.clock import day_bounds_utc


def leaves_for_day(db: Session, staff_ids: list[UUID], day: date_type) -> dict[UUID, list[tuple]]:
    """staff_id -> [(start, end), ...] leave windows overlapping `day`.

    Each window is already clipped to the caller's staff set; overlap with the
    local day is decided by the SQL filter, not clipped to the day boundary
    (the raw window is what timelines and per-slot caps compare against).
    """
    if not staff_ids:
        return {}
    day_start, day_end = day_bounds_utc(day)
    rows = (
        db.query(StaffLeaveModel)
        .filter(
            StaffLeaveModel.staff_id.in_(staff_ids),
            StaffLeaveModel.start_time < day_end,
            StaffLeaveModel.end_time > day_start,
        )
        .all()
    )
    windows: dict[UUID, list[tuple]] = {}
    for row in rows:
        windows.setdefault(row.staff_id, []).append((row.start_time, row.end_time))
    return windows


def full_day_leave_staff(db: Session, day: date_type) -> set[UUID]:
    """Technicians whose leave covers the entire local day - the allocator skips
    placing them, so Step A never hands their branch demand it cannot serve."""
    day_start, day_end = day_bounds_utc(day)
    rows = (
        db.query(StaffLeaveModel.staff_id)
        .filter(
            StaffLeaveModel.start_time <= day_start,
            StaffLeaveModel.end_time >= day_end,
        )
        .all()
    )
    return {row[0] for row in rows}
