"""The allocator counts chairs, so a re-run can no longer double-book one.

Selling counts chairs on the widest possible span, and assignment only ever
shrinks legs - so a freshly sold day always fits its chairs and the nightly
run never used to look at them. But the day's stored spans SHRINK at 21:00,
walk-ins are then sold into the space freed, and a sick call can hand a shrunk
leg back to a slower technician. That chain re-grows a span after the chair
maths was done: two customers, one chair, and nothing used to notice.

Now both places that grow a span - the re-run and a manual reassignment -
count chairs first. A leg with no chair stays unassigned for the manager,
which is the honest outcome of a sick call, not a silent double-booking.
"""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.allocation.application.materialize import materialize_day, release_staff_assignments
from app.allocation.application.reassign import ReassignConflictError, reassign_leg
from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.bookings.infrastructure.models import (
    BookingDetailModel,
    BookingModel,
    BookingStatus,
)
from app.schedule.application.manage import add_appointment
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import shop_timezone
from app.staff.infrastructure.models import StaffModel


def _tech(db_session, cleanup_records, branch_id, name):
    user = UserModel(
        phone_number=f"09{uuid.uuid4().int % 10**8:08d}",
        status=UserStatus.ACTIVE,
        role=UserRole.STAFF,
    )
    db_session.add(user)
    db_session.flush()
    staff = StaffModel(user_id=user.id, branch_id=branch_id, display_name=name)
    db_session.add(staff)
    db_session.commit()
    cleanup_records.append(("users", user.id))
    cleanup_records.append(("staff", staff.id))
    return staff


def _pedi_service(db_session, cleanup_records, branch_id):
    service = ServiceModel(
        branch_id=branch_id,
        name="Chair Pedi",
        category="gel",
        duration_min=60,
        base_price=30.0,
        skill_group="PEDI",
        resource="PEDI_CHAIR",
    )
    db_session.add(service)
    db_session.commit()
    cleanup_records.append(("services", service.id))
    return service


@pytest.fixture
def one_chair(db_session, seeded_branch):
    db_session.execute(
        text("UPDATE locations SET pedicure_chairs = 1 WHERE id = :id"), {"id": seeded_branch}
    )
    db_session.commit()
    yield
    db_session.execute(
        text("UPDATE locations SET pedicure_chairs = 0 WHERE id = :id"), {"id": seeded_branch}
    )
    db_session.commit()


def test_a_sick_call_rerun_leaves_a_leg_over_rather_than_double_booking_the_chair(
    client,
    admin_headers,
    customer_headers,
    db_session,
    seeded_branch,
    seeded_staff,
    seeded_shift,
    one_chair,
    cleanup_records,
):
    """The exact chain from production: fast technician shrinks the leg, a
    walk-in fills the freed chair time, the fast technician calls in sick, and
    the re-run hands the first leg to a slow technician for the full hour. The
    chair cannot hold both - one leg must stay unassigned, not overlap."""
    fast = db_session.get(StaffModel, seeded_staff["staff_id"])
    slow_one = _tech(db_session, cleanup_records, seeded_branch, "Slow One")
    slow_two = _tech(db_session, cleanup_records, seeded_branch, "Slow Two")
    service = _pedi_service(db_session, cleanup_records, seeded_branch)

    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [],
            "capability": {
                str(fast.id): {str(service.id): 15},
                str(slow_one.id): {str(service.id): 60},
                str(slow_two.id): {str(service.id): 60},
            },
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    base = seeded_shift["start"].replace(minute=0)
    booked = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [{"service_id": str(service.id), "start_time": base.isoformat()}],
        },
        headers=customer_headers,
    )
    assert booked.status_code == 201, booked.text
    cleanup_records.append(("bookings", booked.json()["id"]))
    first_leg = uuid.UUID(booked.json()["details"][0]["id"])
    cleanup_records.append(("booking_details", first_leg))

    day = base.astimezone(shop_timezone()).date()
    run = materialize_day(db_session, seeded_branch, day)
    cleanup_records.append(("allocation_runs", run.id))
    db_session.expire_all()
    leg = db_session.get(BookingDetailModel, first_leg)
    # Force the shrunk leg onto the fast technician regardless of fairness
    # order - the chain needs the 15-minute version on the books.
    leg.staff_id = fast.id
    leg.end_time = leg.start_time + timedelta(minutes=15)
    leg.duration_min = 15
    db_session.commit()

    walk_in = add_appointment(
        db_session,
        seeded_staff["user_id"],
        seeded_branch,
        [service.id],
        base + timedelta(minutes=30),
        None,
        None,
        "07700900321",
        "Walk In",
    )
    # Cleanup runs in reverse: the customer must go on the list before the
    # bookings that reference them, so the bookings are deleted first.
    cleanup_records.append(("users", walk_in.customer_id))
    cleanup_records.append(("bookings", walk_in.id))
    for detail in db_session.query(BookingDetailModel).filter_by(booking_id=walk_in.id):
        cleanup_records.append(("booking_details", detail.id))

    release_staff_assignments(db_session, fast.id, seeded_branch, day)
    db_session.execute(text("UPDATE staff SET status = 'BLOCKED' WHERE id = :i"), {"i": fast.id})
    db_session.commit()
    rerun = materialize_day(db_session, seeded_branch, day)
    cleanup_records.append(("allocation_runs", rerun.id))

    db_session.expire_all()
    legs = (
        db_session.query(BookingDetailModel)
        .filter(BookingDetailModel.service_id == service.id)
        .all()
    )
    assigned = [leg for leg in legs if leg.staff_id is not None]
    for a in assigned:
        for b in assigned:
            if a.id != b.id:
                assert not (a.start_time < b.end_time and a.end_time > b.start_time), (
                    "two customers were put on the one pedicure chair"
                )
    assert rerun.unassigned_count == 1, "the chair-less leg belongs on the manager's list"

    db_session.execute(text("UPDATE staff SET status = 'ACTIVE' WHERE id = :i"), {"i": fast.id})
    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(fast.id), str(slow_one.id), str(slow_two.id)]},
    )
    db_session.commit()


def test_reassigning_to_a_slower_technician_cannot_outgrow_the_chair(
    client,
    admin_headers,
    customer_identity,
    db_session,
    seeded_branch,
    seeded_staff,
    seeded_shift,
    one_chair,
    cleanup_records,
):
    """Leg one sits on the chair 10:00-10:15 with the fast technician; leg two
    holds it 10:30 onwards. Moving leg one to a slow technician would stretch
    it to 11:00, straight through leg two's chair time - the move must be
    refused, exactly as it would be for a double-booked technician."""
    fast = db_session.get(StaffModel, seeded_staff["staff_id"])
    slow = _tech(db_session, cleanup_records, seeded_branch, "Slow Mover")
    service = _pedi_service(db_session, cleanup_records, seeded_branch)

    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [],
            "capability": {
                str(fast.id): {str(service.id): 15},
                str(slow.id): {str(service.id): 60},
            },
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    # The day the chain leaves behind cannot be built through the sale - the
    # sale's own chair maths refuses it. Pin it directly: two shrunk legs on
    # the fast technician, fifteen minutes each, half an hour apart.
    base = seeded_shift["start"].replace(minute=0)
    legs = []
    for start in (base, base + timedelta(minutes=30)):
        booking = BookingModel(
            customer_id=customer_identity["id"],
            branch_id=seeded_branch,
            booking_date=base.astimezone(shop_timezone()).date(),
            status=BookingStatus.APPROVED,
            total_price=30,
        )
        db_session.add(booking)
        db_session.flush()
        detail = BookingDetailModel(
            booking_id=booking.id,
            service_id=service.id,
            staff_id=fast.id,
            start_time=start,
            end_time=start + timedelta(minutes=15),
            duration_min=15,
            price=30,
        )
        db_session.add(detail)
        db_session.flush()
        cleanup_records.append(("bookings", booking.id))
        cleanup_records.append(("booking_details", detail.id))
        legs.append(detail.id)
    db_session.commit()

    with pytest.raises(ReassignConflictError):
        reassign_leg(db_session, legs[0], slow.id, seeded_staff["user_id"])

    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(fast.id), str(slow.id)]},
    )
    db_session.commit()
