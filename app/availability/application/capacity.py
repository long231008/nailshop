"""Capacity ledger - where every advance booking goes through (doc v3.1/3.2, 3.3b).

Future days are sold against *lanes*, not against named technicians: nobody knows
yet which tech serves which booking (that is decided at the nightly close, doc 4).
A lane is one overlapping customer within one 15-minute slot of one skill group.

Lane cap per slot = floor(CAP_FILL x expected techs of that group) - counting
lanes (not minutes) is what makes the nightly materialize structurally feasible:
"overlapping legs <= techs" implies an interval colouring exists.
"""

from collections import Counter
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.allocation.application.roster import expected_staff
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.capability.application.matrix import ceil_to_grid, load_matrix
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import (
    is_closed_day,
    now_utc,
    shop_timezone,
    today_in_shop_tz,
)
from app.shared.infrastructure.config.settings import settings
from app.staff.infrastructure.models import StaffModel

GRID_MINUTES = 15

ACTIVE_BOOKING_STATUSES = [
    BookingStatus.PENDING,
    BookingStatus.APPROVED,
    BookingStatus.IN_PROGRESS,
    BookingStatus.COMPLETED,
]


class BookingWindow:
    OPEN = "open"
    CLOSED = "closed"  # past the 21:00 cutoff (or the day already started)
    TOO_FAR = "too_far"
    CLOSED_DAY = "closed_day"


def booking_close_utc(target_date: date_type) -> datetime:
    """Day D stops taking bookings at 21:00 local on D-1 (doc 2.1)."""
    close_local = datetime.combine(
        target_date - timedelta(days=1),
        time(hour=settings.BOOKING_CLOSE_HOUR),
        tzinfo=shop_timezone(),
    )
    return close_local.astimezone(timezone.utc)


def booking_window_state(target_date: date_type, now: datetime | None = None) -> str:
    now = now or now_utc()
    today = today_in_shop_tz(now)
    if is_closed_day(target_date):
        return BookingWindow.CLOSED_DAY
    if target_date > today + timedelta(days=settings.BOOKING_HORIZON_DAYS):
        return BookingWindow.TOO_FAR
    if target_date <= today or now >= booking_close_utc(target_date):
        return BookingWindow.CLOSED
    return BookingWindow.OPEN


def _primary_group(cells: dict[UUID, int], services: dict[UUID, ServiceModel]) -> str | None:
    """Each tech counts toward exactly ONE skill group per day, so lane maths
    never double-counts a multi-skilled tech (doc 3.3b, staffing_plan)."""
    counts = Counter(
        services[service_id].skill_group for service_id in cells if service_id in services
    )
    if not counts:
        return None
    return min(counts, key=lambda group: (-counts[group], group))


def matrix_configured_for(staff_list: list[StaffModel], matrix: dict) -> bool:
    """Has the owner filled in capability cells for any of these techs? Until
    then the scheduler runs in legacy mode: menu durations, everyone assumed
    able to do everything. The first saved cell flips the switch (v3.2)."""
    return any(matrix.get(staff.id) for staff in staff_list)


def eligible_staff(
    db: Session, branch_id: UUID, service_id: UUID, day: date_type
) -> list[StaffModel]:
    """Techs expected at the branch on `day` (per the staffing plan) who can do
    the service."""
    staff_list = expected_staff(db, branch_id, day)
    matrix = load_matrix(db)
    if not matrix_configured_for(staff_list, matrix):
        return staff_list
    return [staff for staff in staff_list if service_id in matrix.get(staff.id, {})]


def planning_minutes(db: Session, branch_id: UUID, service_id: UUID, day: date_type) -> int | None:
    """Cautious any-tech hold: the slowest eligible tech's real minutes, snapped
    to the grid. None when nobody expected that day can do the service - the
    service must not be sold for that day (doc 1.1 rule 3)."""
    staff_list = expected_staff(db, branch_id, day)
    if not staff_list:
        return None
    matrix = load_matrix(db)
    if not matrix_configured_for(staff_list, matrix):
        service = db.get(ServiceModel, service_id)
        return ceil_to_grid(service.duration_min)
    minutes = [
        matrix[staff.id][service_id]
        for staff in staff_list
        if service_id in matrix.get(staff.id, {})
    ]
    return ceil_to_grid(max(minutes)) if minutes else None


def expected_group_counts(db: Session, branch_id: UUID, day: date_type) -> dict[str, int]:
    """Expected techs per skill group on `day`, from the staffing plan (pins +
    Step A assignments + home/pool expectations - see roster.expected_staff)."""
    services = {s.id: s for s in db.query(ServiceModel).all()}
    matrix = load_matrix(db)
    counts: dict[str, int] = {}
    for staff in expected_staff(db, branch_id, day):
        group = _primary_group(matrix.get(staff.id, {}), services)
        if group is not None:
            counts[group] = counts.get(group, 0) + 1
    return counts


def lanes_for(expected_techs: int) -> int:
    """floor(CAP_FILL x T): the slack absorbs staffing surprises and keeps
    walk-in lanes open (T>=2 always leaves at least one tech unsold). A
    one-tech shop still sells its one lane - reserving it would sell nothing."""
    if expected_techs <= 0:
        return 0
    return max(1, int(settings.CAP_FILL * expected_techs))


def _slots_covered(start: datetime, end: datetime) -> list[datetime]:
    """The 15' slots a leg overlaps, keyed by slot start (UTC)."""
    grid = timedelta(minutes=GRID_MINUTES)
    aligned = start - timedelta(
        minutes=start.minute % GRID_MINUTES,
        seconds=start.second,
        microseconds=start.microsecond,
    )
    slots = []
    cursor = aligned
    while cursor < end:
        slots.append(cursor)
        cursor += grid
    return slots


def existing_legs(db: Session, branch_id: UUID, day: date_type) -> list[dict]:
    """All booked legs of the branch on one local day, buffers included."""
    day_start = datetime.combine(day, time.min, tzinfo=shop_timezone()).astimezone(timezone.utc)
    day_end = day_start + timedelta(days=1)
    rows = (
        db.query(BookingDetailModel, ServiceModel)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .join(ServiceModel, BookingDetailModel.service_id == ServiceModel.id)
        .filter(
            BookingModel.branch_id == branch_id,
            BookingModel.status.in_(ACTIVE_BOOKING_STATUSES),
            BookingDetailModel.start_time < day_end,
            BookingDetailModel.end_time > day_start,
        )
        .all()
    )
    return [
        {
            "service_id": service.id,
            "skill_group": service.skill_group,
            "start": detail.start_time,
            "end": detail.end_time + timedelta(minutes=service.buffer_after_min),
        }
        for detail, service in rows
    ]


class CapacityLedger:
    """Snapshot of one (branch, day): lane caps and lanes already sold."""

    def __init__(self, db: Session, branch_id: UUID, day: date_type):
        self.day = day
        staff_list = expected_staff(db, branch_id, day)
        matrix = load_matrix(db)
        if matrix_configured_for(staff_list, matrix):
            group_counts = expected_group_counts(db, branch_id, day)
            self.lane_caps = {group: lanes_for(count) for group, count in group_counts.items()}
            self.default_cap = 0
        else:
            # Legacy mode (matrix not filled in yet): one pooled lane cap over
            # every tech expected in, whatever the skill group.
            self.lane_caps = {}
            self.default_cap = lanes_for(len(staff_list))
        self.used: dict[tuple[str, datetime], int] = {}
        self.used_per_service: dict[tuple[UUID, datetime], int] = {}
        for leg in existing_legs(db, branch_id, day):
            self._count(leg)

    def _count(self, leg: dict) -> None:
        for slot in _slots_covered(leg["start"], leg["end"]):
            key = (leg["skill_group"], slot)
            self.used[key] = self.used.get(key, 0) + 1
            skey = (leg["service_id"], slot)
            self.used_per_service[skey] = self.used_per_service.get(skey, 0) + 1

    def fits(self, legs: list[dict], service_lane_caps: dict[UUID, int]) -> bool:
        """Would these new legs stay under every lane cap? Legs of the same
        proposal are counted against each other too (parallel steps use 2 lanes).
        service_lane_caps: rare-service guard, floor/at-least-1 of eligible techs.
        """
        pending: dict[tuple[str, datetime], int] = {}
        pending_service: dict[tuple[UUID, datetime], int] = {}
        for leg in legs:
            for slot in _slots_covered(leg["start"], leg["end"]):
                key = (leg["skill_group"], slot)
                pending[key] = pending.get(key, 0) + 1
                cap = self.lane_caps.get(leg["skill_group"], self.default_cap)
                if self.used.get(key, 0) + pending[key] > cap:
                    return False
                skey = (leg["service_id"], slot)
                pending_service[skey] = pending_service.get(skey, 0) + 1
                service_cap = service_lane_caps.get(leg["service_id"])
                if (
                    service_cap is not None
                    and self.used_per_service.get(skey, 0) + pending_service[skey] > service_cap
                ):
                    return False
        return True


def rare_service_cap(eligible_count: int) -> int:
    """Concurrent legs of one service <= its eligible techs, shaved by CAP_FILL
    but never below 1 - one tech's speciality is sellable, just not twice at
    the same instant (doc 1.1 rule 3)."""
    return max(1, int(settings.CAP_FILL * eligible_count))


def staff_timeline_busy(db: Session, staff_id: UUID, day: date_type) -> list[tuple]:
    """Busy windows of one tech's personal timeline (identified-tech bookings)."""
    day_start = datetime.combine(day, time.min, tzinfo=shop_timezone()).astimezone(timezone.utc)
    day_end = day_start + timedelta(days=1)
    rows = (
        db.query(BookingDetailModel, ServiceModel)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .join(ServiceModel, BookingDetailModel.service_id == ServiceModel.id)
        .filter(
            BookingDetailModel.staff_id == staff_id,
            BookingModel.status.in_(ACTIVE_BOOKING_STATUSES),
            BookingDetailModel.start_time < day_end,
            BookingDetailModel.end_time > day_start,
        )
        .all()
    )
    windows = [
        (detail.start_time, detail.end_time + timedelta(minutes=service.buffer_after_min))
        for detail, service in rows
    ]
    windows.sort()
    return windows


def is_window_free(busy: list[tuple], start: datetime, end: datetime) -> bool:
    return all(b_end <= start or b_start >= end for b_start, b_end in busy)
