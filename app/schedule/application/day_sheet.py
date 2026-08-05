"""One day, one sheet per technician - what each of them actually works.

The day grid (daily.py) answers "what is the salon doing", ordered by time and
mixing everyone together. This answers "what am I doing", which is the thing a
technician reads before they come in: the shop to turn up at, and their own
customers in order. It is what the 21:00 run is for - until it has run, legs
have no technician and there is nothing personal to show.
"""

from datetime import date as date_type
from uuid import UUID

from sqlalchemy.orm import Session

from app.allocation.infrastructure.assignments import StaffDayAssignmentModel
from app.allocation.infrastructure.models import AllocationRunModel
from app.auth.infrastructure.models import UserModel
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel
from app.branches.infrastructure.models import LocationModel
from app.custom_designs.infrastructure.models import CustomDesignModel
from app.schedule.application.daily import VISIBLE_STATUSES
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import day_bounds_utc
from app.staff.infrastructure.models import StaffModel


def _rostered(db: Session, day: date_type, branch_id: UUID | None) -> dict[UUID, tuple]:
    """staff_id -> (branch_id, branch_name) from the nightly roster (Step A).

    A technician with an empty day still needs their sheet: it tells them which
    shop to turn up at.
    """
    query = (
        db.query(StaffDayAssignmentModel, LocationModel.name)
        .join(LocationModel, StaffDayAssignmentModel.branch_id == LocationModel.id)
        .filter(StaffDayAssignmentModel.day == day)
    )
    if branch_id is not None:
        query = query.filter(StaffDayAssignmentModel.branch_id == branch_id)
    return {row.staff_id: (row.branch_id, name) for row, name in query.all()}


def get_day_sheets(
    db: Session, day: date_type, branch_id: UUID | None, only_staff_id: UUID | None
) -> dict:
    """Per-technician sheets for one local day.

    `only_staff_id` narrows it to a single technician - that is how a member of
    staff sees their own day and nobody else's.
    """
    day_start, day_end = day_bounds_utc(day)

    runs = db.query(AllocationRunModel).filter(AllocationRunModel.target_date == day)
    if branch_id is not None:
        runs = runs.filter(AllocationRunModel.branch_id == branch_id)
    allocated = runs.first() is not None

    roster = _rostered(db, day, branch_id)

    query = (
        db.query(BookingDetailModel, BookingModel, ServiceModel.name, UserModel, CustomDesignModel)
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .join(ServiceModel, BookingDetailModel.service_id == ServiceModel.id)
        .join(UserModel, BookingModel.customer_id == UserModel.id)
        .outerjoin(CustomDesignModel, BookingDetailModel.custom_design_id == CustomDesignModel.id)
        .filter(
            BookingModel.status.in_(VISIBLE_STATUSES),
            BookingDetailModel.staff_id.isnot(None),
            BookingDetailModel.start_time >= day_start,
            BookingDetailModel.start_time < day_end,
        )
    )
    if branch_id is not None:
        query = query.filter(BookingModel.branch_id == branch_id)
    if only_staff_id is not None:
        query = query.filter(BookingDetailModel.staff_id == only_staff_id)

    work: dict[UUID, list] = {}
    branch_of_work: dict[UUID, UUID] = {}
    for detail, booking, service_name, customer, design in query.order_by(
        BookingDetailModel.start_time
    ).all():
        name = " ".join(part for part in (customer.first_name, customer.surname) if part)
        work.setdefault(detail.staff_id, []).append(
            {
                "booking_id": booking.id,
                "start_time": detail.start_time,
                "end_time": detail.end_time,
                "service_name": service_name,
                "customer_name": name or None,
                "customer_phone": customer.phone_number,
                "price": float(detail.price),
                "status": booking.status.value,
                "design_image_url": design.image_url if design else None,
                "design_description": design.description if design else None,
            }
        )
        branch_of_work.setdefault(detail.staff_id, booking.branch_id)

    # Anyone on the roster gets a sheet, and so does anyone holding work that
    # day even if the roster missed them - a manual reassignment can put a
    # technician on a day Step A never rostered.
    staff_ids = set(roster) | set(work)
    if only_staff_id is not None:
        staff_ids &= {only_staff_id}
    if not staff_ids:
        return {"date": day, "allocated": allocated, "sheets": []}

    branches = dict(db.query(LocationModel.id, LocationModel.name).all())
    members = db.query(StaffModel).filter(StaffModel.id.in_(staff_ids)).all()

    sheets = []
    for member in members:
        appointments = work.get(member.id, [])
        home_id, home_name = roster.get(member.id, (None, None))
        if home_id is None:
            home_id = branch_of_work.get(member.id)
            home_name = branches.get(home_id)
        sheets.append(
            {
                "staff_id": member.id,
                "staff_name": member.display_name,
                "branch_id": home_id,
                "branch_name": home_name,
                "appointment_count": len(appointments),
                "working_minutes": sum(
                    int((a["end_time"] - a["start_time"]).total_seconds() // 60)
                    for a in appointments
                ),
                "first_start": appointments[0]["start_time"] if appointments else None,
                "last_end": max(a["end_time"] for a in appointments) if appointments else None,
                "appointments": appointments,
            }
        )

    sheets.sort(key=lambda sheet: (sheet["branch_name"] or "", sheet["staff_name"]))
    return {"date": day, "allocated": allocated, "sheets": sheets}
