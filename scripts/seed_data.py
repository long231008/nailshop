"""Populate the local database with the real branch/service catalog.

Run with: venv/Scripts/python.exe scripts/seed_data.py

Static data (branches/services/staff/shifts) is safe to re-run -
existing rows are reused instead of duplicated. Dynamic data (customers,
bookings, payments, queue tickets, custom designs) is always added fresh
so re-running gives you more sample activity to look at.
"""

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.bookings.infrastructure.models import (
    BookingDetailModel,
    BookingDetailStatus,
    BookingModel,
    BookingStatus,
)
from app.branches.infrastructure.models import LocationModel
from app.custom_designs.infrastructure.models import CustomDesignModel
from app.queue.infrastructure.models import QueueTicketModel, QueueTicketStatus, QueueTicketType
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.database.session import SessionLocal
from app.shifts.infrastructure.models import StaffRosterModel
from app.staff.infrastructure.models import StaffModel
from app.webhooks.infrastructure.models import (
    PaymentTransactionModel,
    PaymentTransactionStatus,
    PaymentTransactionType,
)

SHIFT_DAYS_AHEAD = 7
DEPOSIT_PERCENTAGE = 0.3

# name, category, description, duration_min, base_price
MJ_NAILS_SERVICES = [
    ("Normal Polish", "polish", None, 15, 13),
    ("Normal Polish Manicure", "polish", None, 30, 21),
    ("Mini Normal Polish Pedicure", "polish", None, 30, 18),
    ("Full Normal Polish Pedicure", "polish", None, 45, 32),
    ("Manicure with No Colour", "polish", None, 25, 17),
    ("Full Pedicure with No Colour", "polish", None, 40, 30),
    ("Gel", "gel", "+£1 for existing gel removal", 30, 26),
    ("Gel Manicure", "gel", "Free removal of existing gel", 45, 32),
    ("Mini Gel Pedicure", "gel", "+£1 for existing gel removal", 35, 26),
    ("Full Gel Pedicure", "gel", "Free removal of existing gel", 50, 40),
    ("Acrylics with Normal Polish - Full Set", "full_set", None, 75, 35),
    ("Acrylics with Gel - Full Set", "full_set", None, 90, 40),
    ("Pink and White (Powder French Tips) - Full Set", "full_set", None, 100, 43),
    ("Ombre - Full Set", "full_set", None, 100, 42),
    ("Coloured Acrylic - Full Set", "full_set", None, 90, 42),
    ("Single Nail", "full_set", "From £5", 15, 5),
    ("Acrylic Infill with No Colour", "infill", None, 60, 25),
    ("Acrylics with Normal Polish - Infill", "infill", None, 60, 25),
    ("Acrylics with Gel - Infill", "infill", None, 75, 30),
    ("Pink and White (Powder French Tips) - Infill", "infill", None, 80, 35),
    ("Ombre Infills", "infill", None, 80, 35),
    ("Acrylic Removal", "removal", None, 20, 13),
    ("Gel Removal", "removal", None, 15, 8),
    ("Acrylic Removal and New Set of Acrylics with Normal Polish", "removal", None, 90, 38),
    ("Acrylic Removal and New Set of Acrylics with Gel", "removal", None, 105, 43),
    ("Acrylic Removal and New Set of Ombre", "removal", None, 110, 43),
    ("Acrylic Removal and Normal Polish", "removal", "+£10 with Manicure", 40, 20),
    ("Acrylic Removal and Gel", "removal", None, 50, 30),
    ("Gel Removal and Normal Polish", "removal", "+£10 with Manicure", 35, 16),
    (
        "Acrylic Removal and New Set of Pink and White (Powder French Tips)",
        "removal",
        None,
        110,
        45,
    ),
    ("BIAB Removal and New Set of BIAB", "removal", None, 90, 39),
    ("Overlay on Natural Nails (BIAB)", "biab", None, 60, 34),
    ("BIAB with Nail Extension Tips", "biab", None, 90, 40),
    ("BIAB Ombre Full Set", "biab", None, 100, 45),
    ("BIAB Ombre Infill", "biab", None, 80, 38),
    ("BIAB Infill (with BIAB Colour)", "biab", None, 60, 30),
    ("BIAB Infill (with Gel Colour)", "biab", None, 60, 31),
    ("French BIAB", "biab", "From £5", 20, 5),
    ("Nail Art", "addon", "From £3 - ask staff for exact price before your service", 10, 3),
    ("Gemstones", "addon", "From £3 - ask staff for exact price before your service", 10, 3),
    (
        "French Tips or Coloured Tips",
        "addon",
        "From £5 - ask staff for exact price before your service",
        15,
        5,
    ),
    ("Chrome Effect", "addon", "Add-on, +£6", 10, 6),
    ("Cat Eye Effect", "addon", "Add-on, +£6", 10, 6),
    ("Long Nails", "addon", "Add-on, +£5", 10, 5),
]

# Same catalog as MJ Nails, with a handful of branch-specific prices and one
# extra infill line - matches the real "Major Nails" price list flyer.
_MAJOR_NAILS_OVERRIDES = {
    "Full Gel Pedicure": 39,
    "Acrylics with Gel - Full Set": 38,
    "Pink and White (Powder French Tips) - Full Set": 42,
    "Ombre - Full Set": 40,
    "Coloured Acrylic - Full Set": 39,
    "Acrylic Removal and New Set of Acrylics with Gel": 42,
    "Acrylic Removal and New Set of Ombre": 42,
    "BIAB Removal and New Set of BIAB": 37,
}
MAJOR_NAILS_SERVICES = [
    (name, category, description, duration_min, _MAJOR_NAILS_OVERRIDES.get(name, base_price))
    for name, category, description, duration_min, base_price in MJ_NAILS_SERVICES
] + [
    ("Change Gel Colour on Acrylics", "infill", None, 30, 30),
]

XINH_STUDIO_SERVICES = [
    (
        "Hydro Head Massage - 45 min",
        "hydro_head_massage",
        "Hair & Scalp Consultation, Scalp Oil Treatment, Neck & Shoulder Massage, "
        "Head Massage, Shampoo & Conditioning, Halo Waterfall Experience, Blow-Dry Finish",
        45,
        45,
    ),
    (
        "Hydro Head Massage - 60 min",
        "hydro_head_massage",
        "Hair & Scalp Consultation, Scalp Oil Treatment, Neck & Shoulder Massage, "
        "Head Massage, Facial Cleanse & Mask, Herbal Scalp Rinse, Hair Conditioning, "
        "Halo Waterfall Experience, Blow-Dry Finish",
        60,
        60,
    ),
    (
        "Hydro Head Massage - 90 min",
        "hydro_head_massage",
        "Hair & Scalp Consultation, Scalp Oil Treatment, Neck & Shoulder Massage, "
        "Scalp Combing & Stimulation, Head Massage, Facial Cleanse, Mask & Gua Sha, "
        "Shampoo, Hair Mask, Herbal Scalp Rinse, Hair Conditioning, "
        "Halo Waterfall Experience, Hand Massage, Blow-Dry Finish",
        90,
        85,
    ),
]

BRANCHES = [
    {
        "name": "Major Nails",
        "address": None,
        "phone_number": None,
        "services": MAJOR_NAILS_SERVICES,
        "staff_names": ["Amy", "Kate"],
    },
    {
        "name": "MJ Nails",
        "address": None,
        "phone_number": None,
        "services": MJ_NAILS_SERVICES,
        "staff_names": ["Jess", "Lily"],
    },
    {
        "name": "XINH STUDIO",
        "address": "Unit 3, The Precinct, Boverton Road, Llantwit Major, CF61 1XA",
        "phone_number": "01446621342",
        "services": XINH_STUDIO_SERVICES,
        "staff_names": ["Linh"],
    },
]

# (days_ago relative to now, booking status, payment: None | "deposit" | "deposit+final")
# Spread across today / this week / this month / this year so the dashboard's
# day-week-month-year revenue buckets each show something for every branch.
BOOKING_SCENARIOS = [
    (0, BookingStatus.COMPLETED, "deposit+final"),
    (2, BookingStatus.COMPLETED, "deposit"),
    (10, BookingStatus.COMPLETED, "deposit+final"),
    (60, BookingStatus.COMPLETED, "deposit+final"),
    (0, BookingStatus.PENDING, None),
]


def _seed_branch(db, branch_data: dict):
    branch = db.query(LocationModel).filter_by(name=branch_data["name"]).first()
    if branch is None:
        branch = LocationModel(
            name=branch_data["name"],
            address=branch_data["address"],
            phone_number=branch_data["phone_number"],
        )
        db.add(branch)
        db.flush()

    services = []
    for name, category, description, duration_min, base_price in branch_data["services"]:
        service = db.query(ServiceModel).filter_by(branch_id=branch.id, name=name).first()
        if service is None:
            service = ServiceModel(
                branch_id=branch.id,
                name=name,
                category=category,
                description=description,
                duration_min=duration_min,
                base_price=base_price,
            )
            db.add(service)
            db.flush()
        services.append(service)

    staff_members = []
    for name in branch_data["staff_names"]:
        phone = f"07{uuid.uuid5(uuid.NAMESPACE_DNS, branch_data['name'] + name).int % 10**9:09d}"
        user = db.query(UserModel).filter_by(phone_number=phone).first()
        if user is None:
            user = UserModel(phone_number=phone, status=UserStatus.ACTIVE, role=UserRole.STAFF)
            db.add(user)
            db.flush()
        staff = db.query(StaffModel).filter_by(user_id=user.id).first()
        if staff is None:
            staff = StaffModel(user_id=user.id, branch_id=branch.id, display_name=name)
            db.add(staff)
            db.flush()
        staff_members.append(staff)

    now = datetime.now(timezone.utc)
    shift_count = 0
    for staff in staff_members:
        for day_offset in range(SHIFT_DAYS_AHEAD):
            day = now + timedelta(days=day_offset)
            start = day.replace(hour=9, minute=0, second=0, microsecond=0)
            end = day.replace(hour=18, minute=0, second=0, microsecond=0)
            exists = (
                db.query(StaffRosterModel)
                .filter_by(staff_id=staff.id, start_time=start, end_time=end)
                .first()
            )
            if not exists:
                db.add(
                    StaffRosterModel(
                        staff_id=staff.id, branch_id=branch.id, start_time=start, end_time=end
                    )
                )
                shift_count += 1

    db.flush()
    return branch, services, staff_members, shift_count


def _get_or_create_customer(db, phone: str) -> UserModel:
    customer = db.query(UserModel).filter_by(phone_number=phone).first()
    if customer is None:
        customer = UserModel(phone_number=phone, status=UserStatus.ACTIVE, role=UserRole.CUSTOMER)
        db.add(customer)
        db.flush()
    return customer


def _create_booking(db, customer, service, staff, branch_id, start_time, status):
    duration = service.duration_min
    price = float(service.base_price)

    booking = BookingModel(
        customer_id=customer.id,
        branch_id=branch_id,
        booking_date=start_time.date(),
        status=status,
        total_price=price,
    )
    db.add(booking)
    db.flush()

    detail_status = (
        BookingDetailStatus.COMPLETED
        if status == BookingStatus.COMPLETED
        else BookingDetailStatus.PENDING
    )
    db.add(
        BookingDetailModel(
            booking_id=booking.id,
            service_id=service.id,
            staff_id=staff.id,
            start_time=start_time,
            end_time=start_time + timedelta(minutes=duration),
            duration_min=duration,
            price=price,
            status=detail_status,
        )
    )
    return booking


def _seed_branch_activity(db, branch, services, staff_members, customers):
    now = datetime.now(timezone.utc)
    booking_count = 0
    payment_count = 0

    for idx, (days_ago, status, pay) in enumerate(BOOKING_SCENARIOS):
        customer = customers[idx % len(customers)]
        service = services[idx % len(services)]
        staff = staff_members[idx % len(staff_members)]
        start_time = (now - timedelta(days=days_ago)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )

        booking = _create_booking(db, customer, service, staff, branch.id, start_time, status)
        booking_count += 1

        if pay is not None:
            deposit_amount = round(float(service.base_price) * DEPOSIT_PERCENTAGE, 2)
            booking.deposit_amount = deposit_amount
            booking.final_price = float(service.base_price)

            db.add(
                PaymentTransactionModel(
                    booking_id=booking.id,
                    provider_transaction_id=f"seed-deposit-{uuid.uuid4()}",
                    amount=deposit_amount,
                    transaction_type=PaymentTransactionType.DEPOSIT,
                    status=PaymentTransactionStatus.SUCCESS,
                    created_at=start_time - timedelta(hours=2),
                )
            )
            payment_count += 1

            if pay == "deposit+final":
                db.add(
                    PaymentTransactionModel(
                        booking_id=booking.id,
                        provider_transaction_id=f"seed-final-{uuid.uuid4()}",
                        amount=round(float(service.base_price) - deposit_amount, 2),
                        transaction_type=PaymentTransactionType.FINAL_PAYMENT,
                        status=PaymentTransactionStatus.SUCCESS,
                        created_at=start_time,
                    )
                )
                payment_count += 1

    db.add(
        QueueTicketModel(
            ticket_number=f"W-SEED{uuid.uuid4().hex[:6].upper()}",
            branch_id=branch.id,
            ticket_type=QueueTicketType.WALKIN,
            status=QueueTicketStatus.WAITING,
        )
    )

    return booking_count, payment_count


def seed() -> None:
    db = SessionLocal()

    customers = [
        _get_or_create_customer(db, "07911111111"),
        _get_or_create_customer(db, "07922222222"),
    ]

    db.add(
        CustomDesignModel(
            customer_id=customers[0].id,
            image_url="https://res.cloudinary.com/demo/image/upload/sample-summer.jpg",
            description="Summer floral set",
            estimated_price=None,
        )
    )
    db.add(
        CustomDesignModel(
            customer_id=customers[1].id,
            image_url="https://res.cloudinary.com/demo/image/upload/sample-3d.jpg",
            description="3D nail art",
            estimated_price=20.0,
        )
    )

    summary = []
    for branch_data in BRANCHES:
        branch, services, staff_members, shift_count = _seed_branch(db, branch_data)
        booking_count, payment_count = _seed_branch_activity(
            db, branch, services, staff_members, customers
        )
        summary.append(
            (branch.name, branch.id, len(services), len(staff_members), shift_count, booking_count)
        )

    db.commit()
    db.close()

    for name, branch_id, service_count, staff_count, shift_count, booking_count in summary:
        print(
            f"{name} ({branch_id}): {service_count} services, {staff_count} staff, "
            f"{shift_count} new shifts, {booking_count} bookings"
        )
    print(f"Customers: {len(customers)}")
    print("Custom designs: 2 (1 priced, 1 pending)")


if __name__ == "__main__":
    seed()
