"""Run one salon day end to end and print what the scheduler decided.

    venv/Scripts/python.exe scripts/simulate_day.py
    venv/Scripts/python.exe scripts/simulate_day.py --reset

Creating customers through the sign-in flow one at a time is far too slow to
try the scheduler on, so this builds the whole day directly: a salon with a
deliberately uneven capability matrix, customers, and a run of bookings made
through the real booking path - the same capacity, resource and matching checks
a customer would hit. Then it closes the day (Step A + materialize) and prints
each technician's timeline, the fairness ledger, and anything left unassigned.

Everything it creates is named "Simulator", so --reset removes it and nothing
else. It never touches data you made yourself.
"""

import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.allocation.application.materialize import materialize_day
from app.allocation.application.roster import solve_day
from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.bookings.application.create import create_booking
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel
from app.bookings.presentation.schemas import BookingCreateRequest, BookingItemRequest
from app.branches.infrastructure.models import LocationModel
from app.capability.infrastructure.models import StaffCapabilityModel
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import shop_timezone, today_in_shop_tz
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
]


def target_day():
    """Two days out: tomorrow's window shuts at 21:00 tonight, so a run after
    that would have nothing to sell. Sundays are closed, so skip them."""
    day = today_in_shop_tz() + timedelta(days=2)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return day


def reset(db) -> None:
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
    print("Simulator data removed.")


def build_salon(db):
    branch = LocationModel(name=BRANCH_NAME, address="1 Simulation Street", **RESOURCES)
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
    for index, (display_name, cells) in enumerate(TECHNICIANS.items()):
        user = UserModel(
            phone_number=f"{PHONE_PREFIX}{index:06d}",
            status=UserStatus.ACTIVE,
            role=UserRole.STAFF,
        )
        db.add(user)
        db.flush()
        member = StaffModel(user_id=user.id, branch_id=branch.id, display_name=display_name)
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


def place_demand(db, branch, services, customers, day):
    tz = shop_timezone()
    accepted, refused = 0, 0
    print(f"\nSelling {day} (every booking goes through the real capacity check)")
    print("-" * 74)
    for (hour, minute, wanted), customer in zip(DEMAND, customers, strict=True):
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
            print(f"  sold      {label}")
        except Exception as exc:  # noqa: BLE001 - every refusal is worth showing
            refused += 1
            db.rollback()
            print(f"  REFUSED   {label}  -> {exc}")
    print("-" * 74)
    print(f"  {accepted} sold, {refused} refused")


def report(db, branch, day):
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

    for name in sorted(TECHNICIANS):
        legs = by_staff.get(name, [])
        can_do = ", ".join(s.replace("Simulator ", "") for s in TECHNICIANS[name])
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


def main() -> None:
    db = SessionLocal()
    try:
        if "--reset" in sys.argv:
            reset(db)
            return

        if db.query(LocationModel).filter_by(name=BRANCH_NAME).first():
            print("Simulator salon already exists - clearing it first.")
            reset(db)

        day = target_day()
        branch, services, staff = build_salon(db)
        print(f"Built {BRANCH_NAME}: {len(staff)} technicians, {len(services)} services")
        print(f"  chairs/tables/beds: {RESOURCES}")
        for name, cells in TECHNICIANS.items():
            minutes = ", ".join(f"{s.replace('Simulator ', '')} {m}'" for s, m in cells.items())
            print(f"  {name}: {minutes}")

        customers = make_customers(db, len(DEMAND))
        place_demand(db, branch, services, customers, day)

        solve_day(db, day)
        db.commit()
        run = materialize_day(db, branch.id, day)
        print(f"\nAllocation run: assigned={run.assigned_count} unassigned={run.unassigned_count}")

        report(db, branch, day)
        print("\nRun again to redo the day, or --reset to remove all of it.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
