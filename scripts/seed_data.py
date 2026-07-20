"""Populate the local database with example data for manual testing.

Run with: venv/Scripts/python.exe scripts/seed_data.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.branches.infrastructure.models import LocationModel
from app.discounts.infrastructure.models import DiscountModel, DiscountType
from app.services.infrastructure.models import ServiceExtensionModel, ServiceModel
from app.shared.infrastructure.database.session import SessionLocal
from app.shifts.infrastructure.models import StaffRosterModel
from app.staff.infrastructure.models import StaffModel

SHIFT_DAYS_AHEAD = 7


def seed() -> None:
    db = SessionLocal()

    branch = LocationModel(
        name="Chi nhánh Quận 1",
        address="123 Nguyễn Huệ, Quận 1, TP.HCM",
        phone_number="0901234567",
    )
    db.add(branch)
    db.flush()

    services_data = [
        ("Gel Manicure", "manicure", 45, 25.0),
        ("Classic Manicure", "manicure", 30, 15.0),
        ("Classic Pedicure", "pedicure", 40, 30.0),
        ("Gel Pedicure", "pedicure", 50, 35.0),
        ("Nail Art (per nail)", "nail_art", 15, 5.0),
    ]
    services = []
    for name, category, duration, price in services_data:
        service = ServiceModel(
            branch_id=branch.id,
            name=name,
            category=category,
            duration_min=duration,
            base_price=price,
        )
        db.add(service)
        services.append(service)
    db.flush()

    db.add(
        ServiceExtensionModel(
            service_id=services[0].id,
            name="Extra Long",
            extra_price=5.0,
            extra_duration_min=15,
        )
    )

    staff_members = []
    for i, name in enumerate(["Thợ Lan", "Thợ Mai", "Thợ Hương"], start=1):
        user = UserModel(
            phone_number=f"090000000{i}",
            status=UserStatus.ACTIVE,
            role=UserRole.STAFF,
        )
        db.add(user)
        db.flush()
        staff = StaffModel(user_id=user.id, branch_id=branch.id, display_name=name)
        db.add(staff)
        staff_members.append(staff)
    db.flush()

    now = datetime.now(timezone.utc)
    shift_count = 0
    for staff in staff_members:
        for day_offset in range(SHIFT_DAYS_AHEAD):
            day = now + timedelta(days=day_offset)
            start = day.replace(hour=9, minute=0, second=0, microsecond=0)
            end = day.replace(hour=18, minute=0, second=0, microsecond=0)
            db.add(
                StaffRosterModel(
                    staff_id=staff.id, branch_id=branch.id, start_time=start, end_time=end
                )
            )
            shift_count += 1

    db.add(
        DiscountModel(
            name="Big spender gift",
            discount_type=DiscountType.GIFT,
            value=80,
            branch_id=branch.id,
        )
    )

    db.commit()
    branch_name, branch_id = branch.name, branch.id
    db.close()

    print(f"Seeded branch: {branch_name} ({branch_id})")
    print(f"Seeded {len(services)} services, 1 length variant")
    print(f"Seeded {len(staff_members)} staff, {shift_count} shifts ({SHIFT_DAYS_AHEAD} days each)")
    print("Seeded 1 gift discount rule (spend > 80 -> free gift)")


if __name__ == "__main__":
    seed()
