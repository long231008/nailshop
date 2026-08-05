"""Selling capacity is decided by whether the overlapping legs can actually be
shared out among capable technicians - not by a per-skill-group head count.

Both tests below describe days the group-count model got wrong in opposite
directions: one refused a booking two technicians could have served, the other
sold two bookings only one technician could serve.
"""

import uuid

import pytest

from app.allocation.application.materialize import materialize_day
from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import shop_timezone
from app.staff.infrastructure.models import StaffModel


def _cleanup_capabilities(db_session, staff_ids):
    from sqlalchemy import text

    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(s) for s in staff_ids]},
    )
    db_session.commit()


def _service(db_session, cleanup_records, branch_id, name, group):
    service = ServiceModel(
        branch_id=branch_id,
        name=name,
        category="gel",
        duration_min=30,
        base_price=20.0,
        skill_group=group,
    )
    db_session.add(service)
    db_session.commit()
    cleanup_records.append(("services", service.id))
    return service.id


@pytest.fixture
def second_staff(db_session, cleanup_records, seeded_branch):
    user = UserModel(
        phone_number=f"09{uuid.uuid4().int % 10**8:08d}",
        status=UserStatus.ACTIVE,
        role=UserRole.STAFF,
    )
    db_session.add(user)
    db_session.flush()
    staff = StaffModel(user_id=user.id, branch_id=seeded_branch, display_name="Second Tech")
    db_session.add(staff)
    db_session.commit()
    cleanup_records.append(("users", user.id))
    cleanup_records.append(("staff", staff.id))
    return staff


def test_a_second_technician_who_can_do_it_makes_the_slot_sellable(
    client,
    admin_headers,
    customer_headers,
    other_customer_headers,
    db_session,
    seeded_branch,
    seeded_staff,
    second_staff,
    seeded_shift,
    cleanup_records,
):
    """One technician does manicures and pedicures, the other only pedicures.
    Two pedicures at the same time need one technician each, and there are two
    who can do them, so both must sell - even though the all-rounder's "main"
    skill group is the other one."""
    mani = _service(db_session, cleanup_records, seeded_branch, "Mani", "MANI")
    pedi = _service(db_session, cleanup_records, seeded_branch, "Pedi", "PEDI")

    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [],
            "capability": {
                str(seeded_staff["staff_id"]): {str(mani): 30, str(pedi): 30},
                str(second_staff.id): {str(pedi): 30},
            },
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    payload = {
        "branch_id": str(seeded_branch),
        "items": [{"service_id": str(pedi), "start_time": seeded_shift["start"].isoformat()}],
    }
    first = client.post("/app/bookings", json=payload, headers=customer_headers)
    assert first.status_code == 201, first.text
    cleanup_records.append(("bookings", first.json()["id"]))
    cleanup_records.append(("booking_details", first.json()["details"][0]["id"]))

    second = client.post("/app/bookings", json=payload, headers=other_customer_headers)
    assert second.status_code == 201, second.text
    cleanup_records.append(("bookings", second.json()["id"]))
    cleanup_records.append(("booking_details", second.json()["details"][0]["id"]))

    # And the promise holds: the nightly run seats both, one technician each.
    day = seeded_shift["start"].astimezone(shop_timezone()).date()
    run = materialize_day(db_session, seeded_branch, day)
    cleanup_records.append(("allocation_runs", run.id))
    assert run.unassigned_count == 0
    assert run.assigned_count == 2

    _cleanup_capabilities(db_session, [seeded_staff["staff_id"], second_staff.id])


def test_two_services_only_one_technician_can_do_are_not_both_sold(
    client,
    admin_headers,
    customer_headers,
    other_customer_headers,
    db_session,
    seeded_branch,
    seeded_staff,
    second_staff,
    seeded_shift,
    cleanup_records,
):
    """Two different services in one skill group that only the same single
    technician can perform. Counting heads in the group sees two technicians and
    sells both; only one of them can actually be served, so the second must be
    refused rather than left unassigned at the nightly close."""
    specialist_a = _service(db_session, cleanup_records, seeded_branch, "Art A", "MANI")
    specialist_b = _service(db_session, cleanup_records, seeded_branch, "Art B", "MANI")
    plain = _service(db_session, cleanup_records, seeded_branch, "Plain", "MANI")

    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [],
            "capability": {
                str(seeded_staff["staff_id"]): {str(specialist_a): 30, str(specialist_b): 30},
                str(second_staff.id): {str(plain): 30},
            },
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    start = seeded_shift["start"].isoformat()
    first = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [{"service_id": str(specialist_a), "start_time": start}],
        },
        headers=customer_headers,
    )
    assert first.status_code == 201, first.text
    cleanup_records.append(("bookings", first.json()["id"]))
    cleanup_records.append(("booking_details", first.json()["details"][0]["id"]))

    # Only one technician can do either speciality, and they are now busy.
    second = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [{"service_id": str(specialist_b), "start_time": start}],
        },
        headers=other_customer_headers,
    )
    assert second.status_code == 400, second.text

    _cleanup_capabilities(db_session, [seeded_staff["staff_id"], second_staff.id])
