"""Populate the local database with example data for manual testing.

Run with: venv/Scripts/python.exe scripts/seed_data.py

Static data (branch/services/staff/shifts/discount) is safe to re-run -
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
from app.discounts.infrastructure.models import DiscountModel, DiscountType
from app.queue.infrastructure.models import QueueTicketModel, QueueTicketStatus, QueueTicketType
from app.services.infrastructure.models import ServiceExtensionModel, ServiceModel
from app.shared.infrastructure.database.session import SessionLocal
from app.shifts.infrastructure.models import StaffRosterModel
from app.staff.infrastructure.models import StaffModel
from app.webhooks.infrastructure.models import (
    PaymentTransactionModel,
    PaymentTransactionStatus,
    PaymentTransactionType,
)

SHIFT_DAYS_AHEAD = 7
BRANCH_NAME = "Chi nhánh Quận 1"
DEPOSIT_PERCENTAGE = 0.3

# (days_ago relative to now, booking status, payment: None | "deposit" | "deposit+final")
# Spread across today / this week / this month / this year / last year so the
# dashboard's day-week-month-year revenue buckets each show something different.
BOOKING_SCENARIOS = [
    (0, BookingStatus.COMPLETED, "deposit+final"),
    (2, BookingStatus.COMPLETED, "deposit+final"),
    (10, BookingStatus.COMPLETED, "deposit"),
    (60, BookingStatus.COMPLETED, "deposit+final"),
    (400, BookingStatus.COMPLETED, "deposit+final"),
    (0, BookingStatus.PENDING, None),
    (-1, BookingStatus.APPROVED, None),
    (1, BookingStatus.CANCELLED, None),
    (3, BookingStatus.NO_SHOW, None),
]


def _seed_static_data(db):
    branch = db.query(LocationModel).filter_by(name=BRANCH_NAME).first()
    if branch is None:
        branch = LocationModel(
            name=BRANCH_NAME, address="123 Nguyễn Huệ, Quận 1, TP.HCM", phone_number="0901234567"
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
        service = db.query(ServiceModel).filter_by(branch_id=branch.id, name=name).first()
        if service is None:
            service = ServiceModel(
                branch_id=branch.id,
                name=name,
                category=category,
                duration_min=duration,
                base_price=price,
            )
            db.add(service)
            db.flush()
        services.append(service)

    if not db.query(ServiceExtensionModel).filter_by(service_id=services[0].id).first():
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
        phone = f"090000000{i}"
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

    if not db.query(DiscountModel).filter_by(name="Big spender gift").first():
        db.add(
            DiscountModel(
                name="Big spender gift",
                discount_type=DiscountType.GIFT,
                value=80,
                branch_id=branch.id,
            )
        )

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


def _seed_dynamic_data(db, branch, services, staff_members):
    customers = [
        _get_or_create_customer(db, "0911111111"),
        _get_or_create_customer(db, "0922222222"),
    ]

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

    for _ in range(2):
        db.add(
            QueueTicketModel(
                ticket_number=f"W-SEED{uuid.uuid4().hex[:6].upper()}",
                branch_id=branch.id,
                ticket_type=QueueTicketType.WALKIN,
                status=QueueTicketStatus.WAITING,
            )
        )

    db.add(
        CustomDesignModel(
            customer_id=customers[0].id,
            image_url="https://res.cloudinary.com/demo/image/upload/sample-summer.jpg",
            description="Hoa văn mùa hè",
            estimated_price=None,
        )
    )
    db.add(
        CustomDesignModel(
            customer_id=customers[1].id,
            image_url="https://res.cloudinary.com/demo/image/upload/sample-3d.jpg",
            description="Nail art 3D",
            estimated_price=20.0,
        )
    )

    return len(customers), booking_count, payment_count


def seed() -> None:
    db = SessionLocal()

    branch, services, staff_members, shift_count = _seed_static_data(db)
    customer_count, booking_count, payment_count = _seed_dynamic_data(
        db, branch, services, staff_members
    )

    db.commit()
    branch_name, branch_id = branch.name, branch.id
    db.close()

    print(f"Branch: {branch_name} ({branch_id})")
    print(f"Services: {len(services)}, staff: {len(staff_members)}, new shifts: {shift_count}")
    print(f"Customers: {customer_count}")
    print(
        f"Bookings: {booking_count} (spanning today / this week / this month / this year / last year)"
    )
    print(f"Payment transactions: {payment_count}")
    print("Queue tickets: 2 walk-in waiting")
    print("Custom designs: 2 (1 priced, 1 pending)")


if __name__ == "__main__":
    seed()
