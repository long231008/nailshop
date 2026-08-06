"""Selling on each technician's own minutes, not the slowest one's.

A leg is sold before anyone knows who will perform it, so it used to hold the
slowest capable technician's time everywhere - and the salon refused bookings
its fast technician could easily have absorbed. Matching now tries each
technician at their own pace, exactly the numbers the 21:00 run assigns with.

Two things deliberately do NOT widen. Physical resources (chairs, tables,
beds) stay counted on the cautious span: the nightly run never checks them, so
their safety rests on selling having counted the widest span any assignment
can use. And when the exact solve runs out of search budget, the verdict falls
back to the cautious planned-span rule rather than guessing.
"""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

import app.availability.application.capacity as capacity
from app.allocation.application.materialize import materialize_day
from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import shop_timezone
from app.staff.infrastructure.models import StaffModel


def _service(db_session, cleanup_records, branch_id, name, resource=None):
    service = ServiceModel(
        branch_id=branch_id,
        name=name,
        category="gel",
        duration_min=30,
        base_price=20.0,
        skill_group="MANI",
        resource=resource,
    )
    db_session.add(service)
    db_session.commit()
    cleanup_records.append(("services", service.id))
    return service


@pytest.fixture
def slow_tech(db_session, cleanup_records, seeded_branch):
    user = UserModel(
        phone_number=f"09{uuid.uuid4().int % 10**8:08d}",
        status=UserStatus.ACTIVE,
        role=UserRole.STAFF,
    )
    db_session.add(user)
    db_session.flush()
    staff = StaffModel(user_id=user.id, branch_id=seeded_branch, display_name="Slow Tech")
    db_session.add(staff)
    db_session.commit()
    cleanup_records.append(("users", user.id))
    cleanup_records.append(("staff", staff.id))
    return staff


def _save_matrix(client, admin_headers, capability):
    response = client.put(
        "/app/capability/matrix",
        json={"services": [], "staff": [], "capability": capability},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text


def _book(client, headers, branch_id, service_id, start, cleanup_records):
    response = client.post(
        "/app/bookings",
        json={
            "branch_id": str(branch_id),
            "items": [{"service_id": str(service_id), "start_time": start.isoformat()}],
        },
        headers=headers,
    )
    if response.status_code == 201:
        cleanup_records.append(("bookings", response.json()["id"]))
        cleanup_records.append(("booking_details", response.json()["details"][0]["id"]))
    return response


def test_a_fast_technician_widens_what_the_day_can_sell(
    client,
    admin_headers,
    customer_headers,
    other_customer_headers,
    db_session,
    seeded_branch,
    seeded_staff,
    slow_tech,
    seeded_shift,
    cleanup_records,
):
    """Fast does it in 30', slow in 60', so every leg is PLANNED at 60'. Three
    bookings at 10:00, 10:15 and 10:45: under planned spans the 10:45-11:00
    slots hold three legs against two technicians - refused. At real minutes
    the fast technician absorbs two of them, and the nightly run proves it."""
    service = _service(db_session, cleanup_records, seeded_branch, "Widening")
    _save_matrix(
        client,
        admin_headers,
        {
            str(seeded_staff["staff_id"]): {str(service.id): 30},
            str(slow_tech.id): {str(service.id): 60},
        },
    )

    base = seeded_shift["start"].replace(minute=0)
    first = _book(client, customer_headers, seeded_branch, service.id, base, cleanup_records)
    assert first.status_code == 201, first.text
    second = _book(
        client,
        other_customer_headers,
        seeded_branch,
        service.id,
        base + timedelta(minutes=15),
        cleanup_records,
    )
    assert second.status_code == 201, second.text

    third = _book(
        client,
        customer_headers,
        seeded_branch,
        service.id,
        base + timedelta(minutes=45),
        cleanup_records,
    )
    assert third.status_code == 201, (
        "refused a booking the fast technician could absorb: " + third.text
    )

    # The promise holds: the 21:00 run seats all three.
    day = base.astimezone(shop_timezone()).date()
    run = materialize_day(db_session, seeded_branch, day)
    cleanup_records.append(("allocation_runs", run.id))
    assert run.unassigned_count == 0

    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(seeded_staff["staff_id"]), str(slow_tech.id)]},
    )
    db_session.commit()


def test_an_incapable_fast_technician_widens_nothing(
    client,
    admin_headers,
    customer_headers,
    other_customer_headers,
    db_session,
    seeded_branch,
    seeded_staff,
    slow_tech,
    seeded_shift,
    cleanup_records,
):
    """The fast technician cannot do this service, so the only real pace is the
    slow one's - the same three bookings must still be refused at the third."""
    ours = _service(db_session, cleanup_records, seeded_branch, "Slow Only")
    other = _service(db_session, cleanup_records, seeded_branch, "Elsewhere")
    _save_matrix(
        client,
        admin_headers,
        {
            str(seeded_staff["staff_id"]): {str(other.id): 15},  # fast, but elsewhere
            str(slow_tech.id): {str(ours.id): 60},
        },
    )

    base = seeded_shift["start"].replace(minute=0)
    first = _book(client, customer_headers, seeded_branch, ours.id, base, cleanup_records)
    assert first.status_code == 201, first.text

    second = _book(
        client,
        other_customer_headers,
        seeded_branch,
        ours.id,
        base + timedelta(minutes=15),
        cleanup_records,
    )
    assert second.status_code == 400, "sold a slot only an incapable technician was fast enough for"

    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(seeded_staff["staff_id"]), str(slow_tech.id)]},
    )
    db_session.commit()


def test_chairs_stay_counted_on_the_cautious_span(
    client,
    admin_headers,
    customer_headers,
    other_customer_headers,
    db_session,
    seeded_branch,
    seeded_staff,
    slow_tech,
    seeded_shift,
    cleanup_records,
):
    """One pedicure chair. The fast technician would free it in 15 minutes, but
    the nightly run does not know about chairs - if it hands the leg to the slow
    technician, the chair is taken for the hour. So the chair must be counted at
    the widest span, and the overlapping second pedicure refused."""
    db_session.execute(
        text("UPDATE locations SET pedicure_chairs = 1 WHERE id = :id"), {"id": seeded_branch}
    )
    db_session.commit()
    service = _service(db_session, cleanup_records, seeded_branch, "Pedi", resource="PEDI_CHAIR")
    _save_matrix(
        client,
        admin_headers,
        {
            str(seeded_staff["staff_id"]): {str(service.id): 15},
            str(slow_tech.id): {str(service.id): 60},
        },
    )

    base = seeded_shift["start"].replace(minute=0)
    first = _book(client, customer_headers, seeded_branch, service.id, base, cleanup_records)
    assert first.status_code == 201, first.text

    second = _book(
        client,
        other_customer_headers,
        seeded_branch,
        service.id,
        base + timedelta(minutes=30),
        cleanup_records,
    )
    assert second.status_code == 400, (
        "sold the only chair on the fast technician's pace - the nightly run "
        "may hand the first pedicure to the slow one"
    )

    db_session.execute(
        text("UPDATE locations SET pedicure_chairs = 0 WHERE id = :id"), {"id": seeded_branch}
    )
    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(seeded_staff["staff_id"]), str(slow_tech.id)]},
    )
    db_session.commit()


def test_budget_exhaustion_falls_back_to_the_cautious_rule(
    client,
    admin_headers,
    customer_headers,
    other_customer_headers,
    db_session,
    seeded_branch,
    seeded_staff,
    slow_tech,
    seeded_shift,
    cleanup_records,
    monkeypatch,
):
    """With no budget the solve can settle nothing, so the widening test's third
    booking must be refused exactly as the planned-span rule always did."""
    monkeypatch.setattr(capacity, "SOLVE_BUDGET", 0)

    service = _service(db_session, cleanup_records, seeded_branch, "No Budget")
    _save_matrix(
        client,
        admin_headers,
        {
            str(seeded_staff["staff_id"]): {str(service.id): 30},
            str(slow_tech.id): {str(service.id): 60},
        },
    )

    base = seeded_shift["start"].replace(minute=0)
    first = _book(client, customer_headers, seeded_branch, service.id, base, cleanup_records)
    assert first.status_code == 201, first.text
    second = _book(
        client,
        other_customer_headers,
        seeded_branch,
        service.id,
        base + timedelta(minutes=15),
        cleanup_records,
    )
    assert second.status_code == 201, second.text

    third = _book(
        client,
        customer_headers,
        seeded_branch,
        service.id,
        base + timedelta(minutes=45),
        cleanup_records,
    )
    assert third.status_code == 400, "an unsettled search must fall back to the cautious verdict"

    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(seeded_staff["staff_id"]), str(slow_tech.id)]},
    )
    db_session.commit()
