"""Capacity ledger - where every advance booking goes through (doc v3.1/3.2, 3.3b).

Future days are sold without naming a technician: who serves whom is decided at
the nightly close (doc 4). What the sale must guarantee is only that *some*
valid assignment exists - not which one.

So a time is sellable when the legs overlapping it can be handed to distinct
technicians, each able to perform their own service. That is a bipartite
matching between legs and capable, present technicians, checked per 15-minute
slot. Asking the real question keeps the nightly materialize feasible without
the two errors a per-skill-group head count makes: it undercounts an
all-rounder (who can only be counted in one group, so a slot two technicians
could cover looks full) and overcounts a jagged matrix (two specialities only
the same technician can do look like two free lanes).

The shop is appointment-only, so every expected technician is sellable - no
lane is held back for walk-ins.
"""

from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.allocation.application.roster import expected_staff
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.branches.infrastructure.models import LocationModel
from app.capability.application.matrix import ceil_to_grid, load_matrix, working_window
from app.leaves.application.leaves import leaves_for_day
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

# Ceiling on the whole-day search below. Backtracking is exponential in the
# worst case; a busy branch-day is nowhere near it, but the sale path is no
# place to find out.
SOLVE_BUDGET = 20_000

# A service claims one physical spot through its `resource` field; the branch
# holds a fixed count of each. A count of 0 means "not tracked" (unlimited), so
# only positive caps ever constrain a booking.
RESOURCE_CAP_FIELD = {
    "PEDI_CHAIR": "pedicure_chairs",
    "TABLE": "manicure_tables",
    "MASSAGE_BED": "massage_beds",
}


def branch_resource_caps(branch: LocationModel) -> dict[str, int]:
    """resource -> concurrent cap for one branch, keeping only tracked (positive)
    resources so a fresh branch (all zeros) behaves exactly as before."""
    caps: dict[str, int] = {}
    for resource, field in RESOURCE_CAP_FIELD.items():
        value = getattr(branch, field, 0) or 0
        if value > 0:
            caps[resource] = value
    return caps


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


def matrix_configured_for(staff_list: list[StaffModel], matrix: dict) -> bool:
    """Has the owner filled in capability cells for any of these techs? Until
    then the scheduler runs in legacy mode: menu durations, everyone assumed
    able to do everything. The first saved cell flips the switch (v3.2)."""
    return any(matrix.get(staff.id) for staff in staff_list)


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
            "resource": service.resource,
            # Set once the nightly run (or the desk) named a technician: that
            # leg holds that person, not just any capable one.
            "staff_id": detail.staff_id,
            "start": detail.start_time,
            "end": detail.end_time + timedelta(minutes=service.buffer_after_min),
            # The work itself, buffer excluded - what the hours ledger counts.
            "service_end": detail.end_time,
        }
        for detail, service in rows
    ]


def _work_minutes(leg: dict) -> int:
    """The leg's own minutes, buffer excluded - what a week's hours are spent on."""
    return int((leg.get("service_end", leg["end"]) - leg["start"]).total_seconds() // 60)


def _shareable(legs: list[dict], staff_ids: list[UUID], can_do) -> bool:
    """Can every leg be given its own technician? (Kuhn's augmenting path.)

    True exactly when a matching covering all legs exists, so the sale is only
    allowed when the nightly run has at least one way to seat everyone. The
    graphs are a handful of legs against a handful of technicians, so the plain
    algorithm is far cheaper than the query that built the inputs.
    """
    holder: dict[UUID, int] = {}

    def seat(index: int, tried: set) -> bool:
        for staff_id in staff_ids:
            if staff_id in tried or not can_do(staff_id, legs[index]):
                continue
            tried.add(staff_id)
            if staff_id not in holder or seat(holder[staff_id], tried):
                holder[staff_id] = index
                return True
        return False

    return all(seat(index, set()) for index in range(len(legs)))


class CapacityLedger:
    """Snapshot of one (branch, day): who is expected in, and what is booked.

    Two limits stack on every 15-minute slot:
    - the legs overlapping it must be shareable among distinct technicians who
      can each do their own service and are not on leave then;
    - a physical-resource cap (pedicure chairs / tables / massage beds).
    """

    def __init__(self, db: Session, branch_id: UUID, day: date_type):
        self.day = day
        staff_list = expected_staff(db, branch_id, day)
        matrix = load_matrix(db)
        self.configured = matrix_configured_for(staff_list, matrix)
        self.staff_ids = [staff.id for staff in staff_list]
        self.skills = {staff.id: set(matrix.get(staff.id, {})) for staff in staff_list}
        self.leave = leaves_for_day(db, self.staff_ids, day)
        # Part-timers are only in for part of the day, so the lanes they fill
        # have to shrink with them - otherwise the shop sells a five o'clock
        # nobody is left to work.
        self.hours = {staff.id: working_window(staff, day) for staff in staff_list}
        self.week_cap = {staff.id: staff.max_hours_week * 60 for staff in staff_list}
        self.week_used, self.week_owed = week_hours_budget(db, staff_list, branch_id, day)
        self.week_free = sum(
            max(0, self.week_cap[staff_id] - used) for staff_id, used in self.week_used.items()
        )

        branch = db.get(LocationModel, branch_id)
        self.resource_caps = branch_resource_caps(branch) if branch is not None else {}

        self.legs = existing_legs(db, branch_id, day)
        self.booked: dict[datetime, list[dict]] = {}
        self.used_per_resource: dict[tuple[str, datetime], int] = {}
        for leg in self.legs:
            resource = self._resource_of(leg)
            for slot in _slots_covered(leg["start"], leg["end"]):
                self.booked.setdefault(slot, []).append(leg)
                if resource is not None:
                    key = (resource, slot)
                    self.used_per_resource[key] = self.used_per_resource.get(key, 0) + 1

    def _can_do(self, staff_id: UUID, leg: dict) -> bool:
        # Legacy mode (not one capability cell saved yet): everybody does
        # everything, exactly as the scheduler assumed before the matrix.
        return not self.configured or leg["service_id"] in self.skills.get(staff_id, ())

    def _present(self, slot: datetime) -> list[UUID]:
        """Technicians on the floor for this slot: rostered in, inside their own
        working hours, and not on leave."""
        slot_end = slot + timedelta(minutes=GRID_MINUTES)
        return [s for s in self.staff_ids if self._free_between(s, slot, slot_end)]

    def _free_between(self, staff_id: UUID, start: datetime, end: datetime) -> bool:
        work_start, work_end = self.hours[staff_id]
        if start < work_start or end > work_end:
            return False
        return not any(a < end and b > start for a, b in self.leave.get(staff_id, ()))

    def _can_serve(self, staff_id: UUID, leg: dict) -> bool:
        """Could this technician take this leg - the whole of it?

        Whoever gets a leg keeps it from start to finish, so being on the floor
        for the slot being checked is not enough: an hour off in the middle, or
        a shift that ends before the leg does, rules them out for all of it.
        Checking only the slot let the matching hand one leg to two different
        people in two different slots, and sell a day on the strength of it.
        """
        return self._can_do(staff_id, leg) and self._free_between(
            staff_id, leg["start"], leg["end"]
        )

    def _servable(self, slot: datetime, occupants: list[dict]) -> bool:
        pool = self._present(slot)
        free_pool = set(pool)
        open_legs = []
        for leg in occupants:
            named = leg.get("staff_id")
            if named is None:
                open_legs.append(leg)
            elif named in free_pool:
                free_pool.discard(named)  # already promised to that technician
            # A leg named to someone not expected in today is being served
            # outside this plan (a manual move), so it holds nobody here.
        if not open_legs:
            return True
        return _shareable(open_legs, [s for s in pool if s in free_pool], self._can_serve)

    def can_be_staffed(self, legs: list[dict]) -> bool:
        """The exact question, for the moment a booking is actually taken.

        fits() asks it one 15-minute slot at a time, which is cheap enough to
        run for every time on the page. It is a necessary condition, not a
        sufficient one: each slot is matched on its own, so a leg can be covered
        by one technician in one slot and somebody else in the next - a day
        every slot can staff that no single assignment can. Leave, or a shift
        ending mid-leg, is what makes that reachable.

        This solves the whole day instead: one technician per leg, start to
        finish, nobody in two places, nobody past their week. Slow enough that
        it belongs here and not on the availability page - which was always a
        list of suggestions re-checked at this point anyway.
        """
        if not self.fits(legs):
            return False

        day_legs = sorted(self.legs + list(legs), key=lambda leg: leg["start"])
        busy: dict[UUID, list] = {staff_id: [] for staff_id in self.staff_ids}
        spent = dict(self.week_used)
        open_legs = []
        for leg in day_legs:
            named = leg.get("staff_id")
            if named is None:
                open_legs.append(leg)
            elif named in busy:
                busy[named].append(leg)
                spent[named] = spent.get(named, 0) + _work_minutes(leg)

        steps = [SOLVE_BUDGET]

        def seat(index: int) -> bool:
            if index == len(open_legs):
                return True
            if steps[0] <= 0:
                # Out of search, which means "could not disprove", not "cannot
                # be done". Refusing here would lose a booking that is probably
                # fine; letting it through leaves the manager the same tidy-up
                # they already have for a technician calling in sick.
                return True
            steps[0] -= 1
            leg = open_legs[index]
            need = _work_minutes(leg)
            for staff_id in self.staff_ids:
                if not self._can_serve(staff_id, leg):
                    continue
                if spent.get(staff_id, 0) + need > self.week_cap[staff_id]:
                    continue
                if any(
                    held["start"] < leg["end"] and held["end"] > leg["start"]
                    for held in busy[staff_id]
                ):
                    continue
                busy[staff_id].append(leg)
                spent[staff_id] = spent.get(staff_id, 0) + need
                if seat(index + 1):
                    return True
                busy[staff_id].pop()
                spent[staff_id] -= need
            return False

        return seat(0)

    def _resource_of(self, leg: dict) -> str | None:
        """The tracked physical resource a leg needs, or None when its resource
        is untracked at this branch (cap 0) or unset."""
        resource = leg.get("resource")
        return resource if resource in self.resource_caps else None

    def resource_fits(self, legs: list[dict]) -> bool:
        """Physical-resource check only (pedicure chairs / tables / beds), for the
        desk adding an appointment to a named technician: that technician's own
        timeline is checked separately, but a chair or bed is still a hard limit.
        """
        pending_resource: dict[tuple[str, datetime], int] = {}
        for leg in legs:
            resource = self._resource_of(leg)
            if resource is None:
                continue
            for slot in _slots_covered(leg["start"], leg["end"]):
                rkey = (resource, slot)
                pending_resource[rkey] = pending_resource.get(rkey, 0) + 1
                if (
                    self.used_per_resource.get(rkey, 0) + pending_resource[rkey]
                    > self.resource_caps[resource]
                ):
                    return False
        return True

    def fits(self, legs: list[dict]) -> bool:
        """Could the day still be staffed with these legs added?

        Legs of the same proposal count against each other too: two steps of one
        visit that overlap need two technicians, the same as two customers.
        """
        # Hours the team has left this week, against the work it already owes
        # plus this visit. A day can look perfectly staffable slot by slot and
        # still be work nobody has the hours left to do.
        wanted = sum(_work_minutes(leg) for leg in legs)
        if self.week_owed + wanted > self.week_free:
            return False

        touched: dict[datetime, list[dict]] = {}
        pending_resource: dict[tuple[str, datetime], int] = {}
        for leg in legs:
            resource = self._resource_of(leg)
            for slot in _slots_covered(leg["start"], leg["end"]):
                touched.setdefault(slot, []).append(leg)
                if resource is not None:
                    rkey = (resource, slot)
                    pending_resource[rkey] = pending_resource.get(rkey, 0) + 1
                    if (
                        self.used_per_resource.get(rkey, 0) + pending_resource[rkey]
                        > self.resource_caps[resource]
                    ):
                        return False

        return all(
            self._servable(slot, self.booked.get(slot, []) + pending)
            for slot, pending in touched.items()
        )


def week_hours_budget(
    db: Session, staff_list: list, branch_id: UUID, day: date_type
) -> tuple[dict, int]:
    """(minutes each technician has already committed this week, minutes owed).

    max_hours_week is a ceiling the nightly run enforces, but selling never knew
    about it, so the salon could take four bookings a technician has hours for
    two of and find out at 21:00.

    Owed work cannot be charged to anyone yet - a leg with no technician has not
    been given to one - so this is a pool rather than a per-person count: all
    the work the week has taken on must fit in all the hours the team has left.
    That is a weaker test than the nightly run's per-person one, but it is a
    true one, and it is the part selling can know.

    Scoped to this branch, as the ledger is. A technician who floats between
    branches mid-week has hours counted against them everywhere (their ceiling
    is theirs, not the shop's) but only this branch's owed work counted here.
    """
    if not staff_list:
        return {}, 0
    week_start = day - timedelta(days=day.weekday())
    week_end = week_start + timedelta(days=7)
    staff_ids = [staff.id for staff in staff_list]

    committed = dict(
        db.query(
            BookingDetailModel.staff_id,
            func.coalesce(func.sum(BookingDetailModel.duration_min), 0),
        )
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .filter(
            BookingDetailModel.staff_id.in_(staff_ids),
            BookingModel.status.in_(ACTIVE_BOOKING_STATUSES),
            BookingModel.booking_date >= week_start,
            BookingModel.booking_date < week_end,
        )
        .group_by(BookingDetailModel.staff_id)
        .all()
    )
    owed = (
        db.query(func.coalesce(func.sum(BookingDetailModel.duration_min), 0))
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .filter(
            BookingDetailModel.staff_id.is_(None),
            BookingModel.branch_id == branch_id,
            BookingModel.status.in_(ACTIVE_BOOKING_STATUSES),
            BookingModel.booking_date >= week_start,
            BookingModel.booking_date < week_end,
        )
        .scalar()
    )
    return {staff.id: int(committed.get(staff.id, 0)) for staff in staff_list}, int(owed or 0)


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
