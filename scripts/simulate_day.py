"""Run one salon day end to end and print what the scheduler decided.

    venv/Scripts/python.exe scripts/simulate_day.py            # one day, in full
    venv/Scripts/python.exe scripts/simulate_day.py --fuzz 200 # hunt for bugs
    venv/Scripts/python.exe scripts/simulate_day.py --reset    # clear up after

Creating customers through the sign-in flow one at a time is far too slow to
try the scheduler on, so this builds the whole day directly: a salon with a
deliberately uneven capability matrix, customers, and a run of bookings made
through the real booking path - the same capacity, resource and matching checks
a customer would hit. Then it closes the day (Step A + materialize) and prints
each technician's timeline, the fairness ledger, and anything left unassigned.

--fuzz runs random salons instead - part-timers, short weeks, busy days -
checking after each one that no technician is double-booked, seated during
their own leave or outside their hours, given work they cannot do, worked past
their week, or put on a chair the branch has not got; and that every leg sold
has a technician. It stops at the first day that breaks one and prints a seed
to reproduce with.

Seeds worth keeping after touching the scheduler: 23, 28, 33 and 93 each found
an allocator that gave up while an assignment existed; 7, 93 and 101 each found
a day sold that nobody could have worked. All are green - they are the
regression set, not a to-do list.

Everything it creates is named "Simulator", so --reset removes it and nothing
else. It never touches data you made yourself.
"""

import random
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.allocation.application.materialize import materialize_day, release_staff_assignments
from app.allocation.application.roster import solve_day
from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.availability.application.capacity import branch_resource_caps
from app.bookings.application.create import create_booking
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel
from app.bookings.presentation.schemas import BookingCreateRequest, BookingItemRequest
from app.branches.infrastructure.models import LocationModel
from app.capability.application.matrix import ceil_to_grid, working_window
from app.capability.infrastructure.models import StaffCapabilityModel
from app.leaves.application.leaves import leaves_for_day
from app.leaves.infrastructure.models import StaffLeaveModel
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import day_bounds_utc, shop_timezone, today_in_shop_tz
from app.shared.infrastructure.database.session import SessionLocal
from app.staff.infrastructure.models import StaffModel

BRANCH_NAME = "Simulator Salon"
PHONE_PREFIX = "0999"

# name, category, skill_group, resource, menu minutes, price, buffer, turn weight
SERVICES = [
    ("Simulator Gel Mani", "gel", "MANI", "TABLE", 30, 25.0, 5, 1.0),
    ("Simulator Pedicure", "gel", "PEDI", "PEDI_CHAIR", 45, 35.0, 10, 1.0),
    ("Simulator Nail Art", "addon", "DESIGN", "TABLE", 30, 20.0, 5, 2.0),
    ("Simulator Head Massage", "hydro_head_massage", "KHAC", "MASSAGE_BED", 30, 20.0, 5, 1.0),
]

# Deliberately uneven: An covers everything, Binh only pedicures, Chi is the
# slow one and cannot do pedicures. This is what makes the matching visible -
# a group head count would get this day wrong in both directions.
TECHNICIANS = {
    "An": {"Simulator Gel Mani": 30, "Simulator Pedicure": 40, "Simulator Nail Art": 45},
    "Binh": {"Simulator Pedicure": 45},
    "Chi": {"Simulator Gel Mani": 45, "Simulator Nail Art": 60, "Simulator Head Massage": 30},
}

# Physical capacity: one pedicure chair is the pinch point on purpose.
RESOURCES = {"pedicure_chairs": 1, "manicure_tables": 3, "massage_beds": 1}

# Technician, from, to (shop-local). An is the all-rounder, so taking them out
# of the afternoon is what makes the day interesting: capacity has to stop
# counting them and the allocator has to work around them.
LEAVE = ("An", (13, 0), (17, 0))

# hour, minute, [service names] - one customer each, so the 2h daily cap per
# customer never gets in the way of what is being demonstrated.
DEMAND = [
    (9, 0, ["Simulator Gel Mani"]),
    (9, 0, ["Simulator Pedicure"]),
    (9, 0, ["Simulator Pedicure"]),  # second chair does not exist - expect a refusal
    (10, 0, ["Simulator Gel Mani", "Simulator Nail Art"]),
    (10, 30, ["Simulator Pedicure"]),
    (11, 0, ["Simulator Head Massage"]),
    (11, 0, ["Simulator Gel Mani"]),
    (13, 0, ["Simulator Nail Art"]),
    (13, 30, ["Simulator Pedicure"]),
    (14, 0, ["Simulator Gel Mani"]),
    (15, 0, ["Simulator Nail Art"]),
    (15, 0, ["Simulator Head Massage"]),
    (16, 0, ["Simulator Pedicure"]),  # An is on leave - only Binh is left
    (16, 0, ["Simulator Gel Mani"]),
    (17, 0, ["Simulator Gel Mani", "Simulator Nail Art"]),
]


def target_day():
    """Two days out: tomorrow's window shuts at 21:00 tonight, so a run after
    that would have nothing to sell. Sundays are closed, so skip them."""
    day = today_in_shop_tz() + timedelta(days=2)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return day


def reset(db, quiet=False) -> None:
    branch = db.query(LocationModel).filter_by(name=BRANCH_NAME).first()
    staff_ids = [
        row[0]
        for row in db.query(StaffModel.id)
        .join(UserModel, StaffModel.user_id == UserModel.id)
        .filter(UserModel.phone_number.like(f"{PHONE_PREFIX}%"))
        .all()
    ]
    booking_ids = (
        [row[0] for row in db.query(BookingModel.id).filter_by(branch_id=branch.id).all()]
        if branch
        else []
    )

    if booking_ids:
        db.execute(
            text("DELETE FROM booking_details WHERE booking_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [str(b) for b in booking_ids]},
        )
        db.execute(
            text("DELETE FROM audit_logs WHERE entity_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [str(b) for b in booking_ids]},
        )
        db.execute(
            text("DELETE FROM bookings WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [str(b) for b in booking_ids]},
        )
    if staff_ids:
        ids = {"ids": [str(s) for s in staff_ids]}
        db.execute(
            text("DELETE FROM staff_capabilities WHERE staff_id = ANY(CAST(:ids AS uuid[]))"), ids
        )
        db.execute(
            text("DELETE FROM staff_day_assignments WHERE staff_id = ANY(CAST(:ids AS uuid[]))"),
            ids,
        )
        db.execute(text("DELETE FROM staff_leaves WHERE staff_id = ANY(CAST(:ids AS uuid[]))"), ids)
        db.execute(text("DELETE FROM staff WHERE id = ANY(CAST(:ids AS uuid[]))"), ids)
    if branch:
        db.execute(text("DELETE FROM allocation_runs WHERE branch_id = :id"), {"id": branch.id})
        db.execute(text("DELETE FROM slot_locks WHERE branch_id = :id"), {"id": branch.id})
        db.execute(text("DELETE FROM services WHERE branch_id = :id"), {"id": branch.id})
    db.execute(text("DELETE FROM users WHERE phone_number LIKE :p"), {"p": f"{PHONE_PREFIX}%"})
    if branch:
        db.execute(text("DELETE FROM locations WHERE id = :id"), {"id": branch.id})
    db.commit()
    if not quiet:
        print("Simulator data removed.")


def build_salon(db, technicians=TECHNICIANS, resources=RESOURCES, contracts=None):
    """contracts[name] = (work_start_hour, work_end_hour, max_hours_week); a name
    left out is a full-timer on the default weekly ceiling."""
    contracts = contracts or {}
    branch = LocationModel(name=BRANCH_NAME, address="1 Simulation Street", **resources)
    db.add(branch)
    db.flush()

    services = {}
    for name, category, group, resource, minutes, price, buffer_min, weight in SERVICES:
        service = ServiceModel(
            branch_id=branch.id,
            name=name,
            category=category,
            duration_min=minutes,
            base_price=price,
            skill_group=group,
            resource=resource,
            buffer_after_min=buffer_min,
            turn_weight=weight,
        )
        db.add(service)
        db.flush()
        services[name] = service

    staff = {}
    for index, (display_name, cells) in enumerate(technicians.items()):
        user = UserModel(
            phone_number=f"{PHONE_PREFIX}{index:06d}",
            status=UserStatus.ACTIVE,
            role=UserRole.STAFF,
        )
        db.add(user)
        db.flush()
        start_hour, end_hour, week_cap = contracts.get(display_name, (None, None, 40))
        member = StaffModel(
            user_id=user.id,
            branch_id=branch.id,
            display_name=display_name,
            work_start_hour=start_hour,
            work_end_hour=end_hour,
            max_hours_week=week_cap,
        )
        db.add(member)
        db.flush()
        for service_name, real_minutes in cells.items():
            db.add(
                StaffCapabilityModel(
                    staff_id=member.id,
                    service_id=services[service_name].id,
                    minutes=real_minutes,
                )
            )
        staff[display_name] = member

    db.commit()
    return branch, services, staff


def book_leave(db, staff, day, leaves=(LEAVE,), quiet=False):
    tz = shop_timezone()
    for name, (from_hour, from_minute), (to_hour, to_minute) in leaves:
        start = datetime.combine(day, time(from_hour, from_minute), tzinfo=tz)
        end = datetime.combine(day, time(to_hour, to_minute), tzinfo=tz)
        db.add(
            StaffLeaveModel(
                staff_id=staff[name].id,
                start_time=start.astimezone(timezone.utc),
                end_time=end.astimezone(timezone.utc),
            )
        )
        if not quiet:
            print(
                f"  {name} is on leave "
                f"{from_hour:02d}:{from_minute:02d}-{to_hour:02d}:{to_minute:02d}"
            )
    db.commit()


def make_customers(db, count):
    customers = []
    for index in range(count):
        user = UserModel(
            phone_number=f"{PHONE_PREFIX}9{index:05d}",
            first_name=f"Guest{index + 1}",
            status=UserStatus.ACTIVE,
            role=UserRole.CUSTOMER,
        )
        db.add(user)
        db.flush()
        customers.append(user)
    db.commit()
    return customers


def place_demand(db, branch, services, customers, day, demand=DEMAND, quiet=False):
    tz = shop_timezone()
    accepted, refused = 0, 0
    if not quiet:
        print(f"\nSelling {day} (every booking goes through the real capacity check)")
        print("-" * 74)
    for (hour, minute, wanted), customer in zip(demand, customers, strict=True):
        start = datetime.combine(day, time(hour=hour, minute=minute), tzinfo=tz).astimezone(
            timezone.utc
        )
        payload = BookingCreateRequest(
            branch_id=branch.id,
            items=[
                BookingItemRequest(service_id=services[name].id, start_time=start)
                for name in wanted
            ],
        )
        label = (
            f"{hour:02d}:{minute:02d}  {' + '.join(n.replace('Simulator ', '') for n in wanted)}"
        )
        try:
            create_booking(db, customer.id, payload)
            accepted += 1
            if not quiet:
                print(f"  sold      {label}")
        except Exception as exc:  # noqa: BLE001 - every refusal is worth showing
            refused += 1
            db.rollback()
            if not quiet:
                print(f"  REFUSED   {label}  -> {exc}")
    if not quiet:
        print("-" * 74)
        print(f"  {accepted} sold, {refused} refused")


def _sold_legs(db, branch):
    return (
        db.query(BookingDetailModel, ServiceModel)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .join(ServiceModel, BookingDetailModel.service_id == ServiceModel.id)
        .filter(BookingModel.branch_id == branch.id)
        .order_by(BookingDetailModel.start_time)
        .all()
    )


def _complete_assignment_exists(rows, staff_ids, minutes, blocked, caps, budget=200_000):
    """Could every sold leg have had a technician? Backtracking over the same
    rules materialize_day uses - capability, real per-tech minutes, buffers,
    leave, working hours and the weekly ceiling. Resource caps are not re-checked
    here: the legs are already sold, so who performs them is the only question.

    Every rule the allocator obeys has to be in here too, or a day it refused
    for a good reason reads as a day it gave up on."""
    timelines = {staff_id: [] for staff_id in staff_ids}
    spent = dict.fromkeys(staff_ids, 0)
    steps = [budget]

    def seat(index):
        if index == len(rows):
            return True
        if steps[0] <= 0:
            return False
        steps[0] -= 1
        detail, service = rows[index]
        for staff_id in staff_ids:
            real = minutes.get((staff_id, service.id))
            if real is None:
                continue
            real = ceil_to_grid(real)
            if spent[staff_id] + real > caps.get(staff_id, 0):
                continue
            end = detail.start_time + timedelta(minutes=real)
            hold = end + timedelta(minutes=service.buffer_after_min)
            busy = timelines[staff_id] + blocked.get(staff_id, [])
            if not all(b <= detail.start_time or a >= hold for a, b in busy):
                continue
            timelines[staff_id].append((detail.start_time, hold))
            spent[staff_id] += real
            if seat(index + 1):
                return True
            timelines[staff_id].pop()
            spent[staff_id] -= real
        return False

    return seat(0)


def audit(db, branch, day, allow_orphans=False):
    """The invariants a finished schedule must never break.

    This is what makes the simulator a bug finder rather than something to
    squint at: a timeline that looks plausible can still have a technician in
    two rooms at once, or seated during their own leave.
    """
    tz = shop_timezone()
    problems = []

    staff_rows = db.query(StaffModel).filter_by(branch_id=branch.id).all()
    names = {member.id: member.display_name for member in staff_rows}
    minutes = {
        (row.staff_id, row.service_id): row.minutes
        for row in db.query(StaffCapabilityModel).filter(
            StaffCapabilityModel.staff_id.in_(list(names))
        )
    }
    leave = leaves_for_day(db, list(names), day)
    rows = _sold_legs(db, branch)

    # Off-duty hours block a technician exactly as leave does, so the checks
    # below treat them as one thing.
    day_start, day_end = day_bounds_utc(day)
    blocked = {staff_id: list(windows) for staff_id, windows in leave.items()}
    caps = {}
    for member in staff_rows:
        work_start, work_end = working_window(member, day)
        blocked.setdefault(member.id, []).extend([(day_start, work_start), (work_end, day_end)])
        caps[member.id] = member.max_hours_week * 60

    def when(moment):
        return f"{moment.astimezone(tz):%H:%M}"

    for detail, service in rows:
        if detail.staff_id is None:
            continue
        label = service.name.replace("Simulator ", "")
        if (detail.staff_id, service.id) not in minutes:
            problems.append(f"{names[detail.staff_id]} was given {label} but cannot do it")
        for start, end in blocked.get(detail.staff_id, []):
            if detail.start_time < end and detail.end_time > start:
                problems.append(
                    f"{names[detail.staff_id]} was given {label} at {when(detail.start_time)}"
                    f" - they are off until {when(end)}"
                )

    per_staff: dict = {}
    for detail, service in rows:
        if detail.staff_id is not None:
            per_staff.setdefault(detail.staff_id, []).append(
                (
                    detail.start_time,
                    detail.end_time + timedelta(minutes=service.buffer_after_min),
                    service.name.replace("Simulator ", ""),
                )
            )
    for staff_id, windows in per_staff.items():
        windows.sort()
        for (_, first_end, first), (second_start, _, second) in zip(
            windows, windows[1:], strict=False
        ):
            if second_start < first_end:
                problems.append(
                    f"{names[staff_id]} is in two places at once: {first} runs to"
                    f" {when(first_end)} but {second} starts {when(second_start)}"
                )

    for resource, cap in branch_resource_caps(branch).items():
        for tick in sorted({detail.start_time for detail, _ in rows}):
            busy = [
                service.name.replace("Simulator ", "")
                for detail, service in rows
                if detail.staff_id is not None
                and service.resource == resource
                and detail.start_time <= tick < detail.end_time
            ]
            if len(busy) > cap:
                problems.append(
                    f"{resource}: {len(busy)} at once at {when(tick)}"
                    f" ({', '.join(busy)}) but the branch has {cap}"
                )
                break  # one report per resource is enough to go and look

    worked: dict = {}
    for detail, _service in rows:
        if detail.staff_id is not None:
            worked[detail.staff_id] = worked.get(detail.staff_id, 0) + detail.duration_min
    for staff_id, total in worked.items():
        if total > caps.get(staff_id, 0):
            problems.append(
                f"{names[staff_id]} was given {total}m of work but their week allows"
                f" {caps.get(staff_id, 0)}m"
            )

    # Any leg without a technician is a bug; which layer failed depends on
    # whether the day could have been staffed at all. Selling promised it could.
    # After a sick call the promise is void - somebody's work has nowhere to
    # go, that is exactly what the manager's list is for - so the caller may
    # waive this one check while every other invariant still applies.
    orphans = [detail for detail, _ in rows if detail.staff_id is None]
    if orphans and not allow_orphans:
        if _complete_assignment_exists(rows, list(names), minutes, blocked, caps):
            problems.append(
                f"{len(orphans)} leg(s) left unassigned, yet a complete assignment exists"
                " - the allocator gave up too early"
            )
        else:
            problems.append(
                f"{len(orphans)} leg(s) sold that nobody could ever have worked"
                " - selling took on a day the salon cannot staff"
            )

    return list(dict.fromkeys(problems))


def print_audit(problems):
    print("\n" + "=" * 74)
    if problems:
        print(f"  {len(problems)} PROBLEM(S) FOUND")
        for problem in problems:
            print(f"    ! {problem}")
    else:
        print("  All invariants hold: capability, no double-booking, leave, resource caps,")
        print("  and nothing was left unassigned that could have been staffed.")


def report(db, branch, day, technicians=TECHNICIANS):
    print(f"\nAfter the 21:00 close on {day}")
    print("=" * 74)

    tz = shop_timezone()
    rows = (
        db.query(BookingDetailModel, ServiceModel, StaffModel, UserModel)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .join(ServiceModel, BookingDetailModel.service_id == ServiceModel.id)
        .join(UserModel, BookingModel.customer_id == UserModel.id)
        .outerjoin(StaffModel, BookingDetailModel.staff_id == StaffModel.id)
        .filter(BookingModel.branch_id == branch.id)
        .order_by(BookingDetailModel.start_time)
        .all()
    )

    by_staff: dict[str, list] = {}
    turns: dict[str, float] = {}
    unassigned = []
    for detail, service, member, customer in rows:
        local = detail.start_time.astimezone(tz)
        end = detail.end_time.astimezone(tz)
        line = (
            f"    {local:%H:%M}-{end:%H:%M}  {service.name.replace('Simulator ', ''):<14}"
            f"{customer.first_name or 'Guest'}"
        )
        if member is None:
            unassigned.append(line)
        else:
            by_staff.setdefault(member.display_name, []).append(line)
            turns[member.display_name] = turns.get(member.display_name, 0) + float(
                service.turn_weight
            )

    for name in sorted(technicians):
        legs = by_staff.get(name, [])
        can_do = ", ".join(s.replace("Simulator ", "") for s in technicians[name])
        print(f"\n  {name}  (can do: {can_do})")
        print(f"  turns: {turns.get(name, 0):.1f}   jobs: {len(legs)}")
        for line in legs:
            print(line)
        if not legs:
            print("    - nothing -")

    print("\n" + "=" * 74)
    if unassigned:
        print(f"  {len(unassigned)} leg(s) NOBODY could take - a human has to sort these:")
        for line in unassigned:
            print(line)
    else:
        print("  Every sold leg has a technician.")

    spread = f"{min(turns.values()):.1f}-{max(turns.values()):.1f}" if turns else "n/a"
    print(f"  Fairness: turns ranged {spread} across {len(turns)} working technician(s).")


def random_scenario(rng):
    """A salon nobody designed: random skills, speeds, chairs, leave and demand.

    The hand-written day above only ever finds the bugs it was written to find.
    These days were not chosen by anyone, which is the point.
    """
    names = ["An", "Binh", "Chi", "Dung", "Em"][: rng.randint(2, 5)]
    service_names = [row[0] for row in SERVICES]
    technicians = {
        name: {
            service: rng.choice([15, 30, 45, 60])
            for service in rng.sample(service_names, rng.randint(1, len(service_names)))
        }
        for name in names
    }
    resources = {
        "pedicure_chairs": rng.randint(1, 3),
        "manicure_tables": rng.randint(1, 4),
        "massage_beds": rng.randint(1, 2),
    }
    leaves = [
        (rng.choice(names), (hour := rng.randint(9, 15), 0), (hour + rng.randint(1, 4), 0))
        for _ in range(rng.randint(0, 2))
    ]
    # Some of the team are part-timers on short weeks - the shape that makes
    # both hours ceilings bite, and the shape a hand-written day never has.
    contracts = {}
    for name in names:
        if rng.random() < 0.4:
            start = rng.choice([9, 10, 12, 13])
            contracts[name] = (start, start + rng.randint(3, 6), rng.choice([8, 12, 16, 40]))
        else:
            contracts[name] = (None, None, rng.choice([16, 24, 40, 40]))
    demand = [
        (
            rng.randint(9, 16),
            rng.choice([0, 15, 30, 45]),
            rng.sample(service_names, rng.randint(1, 2)),
        )
        for _ in range(rng.randint(10, 28))
    ]
    return technicians, resources, leaves, demand, contracts


def _busiest_staff(db, branch):
    """The technician holding the most legs at this branch - the worst person
    to lose, which is the point of the sick-call wave."""
    counts: dict = {}
    for detail, _service in _sold_legs(db, branch):
        if detail.staff_id is not None:
            counts[detail.staff_id] = counts.get(detail.staff_id, 0) + 1
    if not counts:
        return None
    return db.get(StaffModel, max(counts, key=counts.get))


def fuzz(db, rounds, first_seed) -> bool:
    """Run random days until one breaks an invariant. Returns True if all held."""
    print(f"Fuzzing {rounds} random day(s) from seed {first_seed}\n")
    for offset in range(rounds):
        seed = first_seed + offset
        rng = random.Random(seed)
        technicians, resources, leaves, demand, contracts = random_scenario(rng)

        reset(db, quiet=True)
        day = target_day()
        branch, services, staff = build_salon(db, technicians, resources, contracts)
        book_leave(db, staff, day, leaves, quiet=True)
        customers = make_customers(db, len(demand))
        place_demand(db, branch, services, customers, day, demand, quiet=True)
        solve_day(db, day)
        db.commit()
        materialize_day(db, branch.id, day)

        problems = audit(db, branch, day)

        if not problems:
            sick = _busiest_staff(db, branch)
            if sick is not None:
                release_staff_assignments(db, sick.id, branch.id, day)
                start, end = day_bounds_utc(day)
                db.add(StaffLeaveModel(staff_id=sick.id, start_time=start, end_time=end))
                db.commit()
                materialize_day(db, branch.id, day)
                problems = [
                    f"[after sick call] {p}" for p in audit(db, branch, day, allow_orphans=True)
                ]

        print(f"  seed {seed}: {'OK' if not problems else f'{len(problems)} PROBLEM(S)'}")
        if problems:
            print_audit(problems)
            print("\nThe salon that broke it:")
            print(f"  resources: {resources}")
            for name, cells in technicians.items():
                print(f"  {name}: {cells}")
            print(f"  leave: {leaves}")
            print(f"  contracts (start, end, max h/week): {contracts}")
            print(f"  demand: {demand}")
            report(db, branch, day, technicians)
            print(f"\nReproduce with: scripts/simulate_day.py --fuzz 1 --seed {seed}")
            return False
    print(f"\n{rounds} random day(s), no invariant broken.")
    return True


def _flag(name, fallback):
    if name in sys.argv:
        index = sys.argv.index(name) + 1
        if index < len(sys.argv) and sys.argv[index].isdigit():
            return int(sys.argv[index])
    return fallback


def main() -> None:
    db = SessionLocal()
    try:
        if "--reset" in sys.argv:
            reset(db)
            return

        if db.query(LocationModel).filter_by(name=BRANCH_NAME).first():
            print("Simulator salon already exists - clearing it first.")
            reset(db)

        if "--fuzz" in sys.argv:
            ok = fuzz(db, _flag("--fuzz", 25), _flag("--seed", 1))
            sys.exit(0 if ok else 1)

        day = target_day()
        branch, services, staff = build_salon(db)
        print(f"Built {BRANCH_NAME}: {len(staff)} technicians, {len(services)} services")
        print(f"  chairs/tables/beds: {RESOURCES}")
        for name, cells in TECHNICIANS.items():
            minutes = ", ".join(f"{s.replace('Simulator ', '')} {m}'" for s, m in cells.items())
            print(f"  {name}: {minutes}")

        book_leave(db, staff, day)

        customers = make_customers(db, len(DEMAND))
        place_demand(db, branch, services, customers, day)

        solve_day(db, day)
        db.commit()
        run = materialize_day(db, branch.id, day)
        print(f"\nAllocation run: assigned={run.assigned_count} unassigned={run.unassigned_count}")

        report(db, branch, day)
        print_audit(audit(db, branch, day))
        print("\nRun again to redo the day, or --reset to remove all of it.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
