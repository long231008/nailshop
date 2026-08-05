"""Fairness must not cost a customer their technician.

The nightly run seats legs one at a time, earliest first, and gives each to
whoever holds the fewest turns. That rule on its own will hand a job several
technicians could do to the one technician a later job needs - and the later
job then has nobody, even though a complete assignment existed all along.
Capacity only sold the day because such an assignment existed, so a leg left
over here is the allocator failing to find one rather than an oversell.
"""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.allocation.application.materialize import materialize_day
from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.bookings.infrastructure.models import BookingDetailModel
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import shop_timezone
from app.staff.infrastructure.models import StaffModel


def _service(db_session, cleanup_records, branch_id, name):
    service = ServiceModel(
        branch_id=branch_id,
        name=name,
        category="gel",
        duration_min=30,
        base_price=20.0,
        skill_group="MANI",
    )
    db_session.add(service)
    db_session.commit()
    cleanup_records.append(("services", service.id))
    return service.id


@pytest.fixture
def specialist(db_session, cleanup_records, seeded_branch):
    user = UserModel(
        phone_number=f"09{uuid.uuid4().int % 10**8:08d}",
        status=UserStatus.ACTIVE,
        role=UserRole.STAFF,
    )
    db_session.add(user)
    db_session.flush()
    staff = StaffModel(user_id=user.id, branch_id=seeded_branch, display_name="Specialist")
    db_session.add(staff)
    db_session.commit()
    cleanup_records.append(("users", user.id))
    cleanup_records.append(("staff", staff.id))
    return staff


def test_a_leg_only_one_tech_can_do_displaces_the_job_anyone_could_have_taken(
    client,
    admin_headers,
    customer_headers,
    other_customer_headers,
    db_session,
    seeded_branch,
    seeded_staff,
    specialist,
    seeded_shift,
    cleanup_records,
):
    """Three legs, in the order the nightly run sees them:

    10:00 warmup  - only the all-rounder can do it, so it goes to them
    11:00 shared  - both can do it, and the specialist now looks fairer (0 turns
                    against the all-rounder's 1), so fairness sends it there
    11:15 solo    - only the specialist can do it, and they are now busy

    Left alone, `solo` ends the night with nobody. The repair pass has to see
    that `shared` can move to the all-rounder and free the specialist for the
    one leg only they can do.
    """
    warmup = _service(db_session, cleanup_records, seeded_branch, "Warmup")
    shared = _service(db_session, cleanup_records, seeded_branch, "Shared")
    solo = _service(db_session, cleanup_records, seeded_branch, "Solo")

    all_rounder = seeded_staff["staff_id"]
    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [],
            "capability": {
                str(all_rounder): {str(warmup): 30, str(shared): 30},
                str(specialist.id): {str(shared): 30, str(solo): 30},
            },
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    tz = shop_timezone()
    opening = seeded_shift["start"].astimezone(tz)

    def book(service_id, minutes_in, headers):
        start = opening.replace(minute=0) + timedelta(minutes=minutes_in)
        response = client.post(
            "/app/bookings",
            json={
                "branch_id": str(seeded_branch),
                "items": [{"service_id": str(service_id), "start_time": start.isoformat()}],
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        cleanup_records.append(("bookings", response.json()["id"]))
        detail_id = response.json()["details"][0]["id"]
        cleanup_records.append(("booking_details", detail_id))
        return detail_id

    book(warmup, 0, customer_headers)
    shared_detail = book(shared, 60, customer_headers)
    solo_detail = book(solo, 75, other_customer_headers)

    day = seeded_shift["start"].astimezone(tz).date()
    run = materialize_day(db_session, seeded_branch, day)
    cleanup_records.append(("allocation_runs", run.id))

    assert run.unassigned_count == 0, "the leg only one technician can do was left with nobody"
    assert run.assigned_count == 3

    db_session.expire_all()
    assert db_session.get(BookingDetailModel, solo_detail).staff_id == specialist.id
    assert db_session.get(BookingDetailModel, shared_detail).staff_id == all_rounder

    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(all_rounder), str(specialist.id)]},
    )
    db_session.commit()
