"""Branch physical resources, staff leave, and desk schedule management."""

import uuid
from datetime import timedelta

import pytest

from app.allocation.application.materialize import materialize_day
from app.allocation.application.roster import solve_day
from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.branches.infrastructure.models import LocationModel
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import day_bounds_utc, shop_timezone
from app.staff.infrastructure.models import StaffModel


def _cleanup_capabilities(db_session, staff_ids):
    from sqlalchemy import text

    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(s) for s in staff_ids]},
    )
    db_session.commit()


def _track_booking(db_session, cleanup_records, booking_id):
    """Schedule endpoints return only the booking id, so look up its detail rows
    for teardown (booking_details reference bookings with no cascade)."""
    booking_uuid = uuid.UUID(str(booking_id))
    cleanup_records.append(("bookings", booking_uuid))
    for (detail_id,) in (
        db_session.query(BookingDetailModel.id)
        .filter(BookingDetailModel.booking_id == booking_uuid)
        .all()
    ):
        cleanup_records.append(("booking_details", detail_id))


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


@pytest.fixture
def pedi_service(db_session, cleanup_records, seeded_branch):
    service = ServiceModel(
        branch_id=seeded_branch,
        name="Deluxe Pedicure",
        category="gel",
        duration_min=30,
        base_price=25.0,
        skill_group="PEDI",
        resource="PEDI_CHAIR",
    )
    db_session.add(service)
    db_session.commit()
    cleanup_records.append(("services", service.id))
    return service.id


# --- Branch physical resources ---------------------------------------------


def test_resource_cap_blocks_oversell_even_with_free_techs(
    client,
    admin_headers,
    customer_headers,
    other_customer_headers,
    db_session,
    seeded_branch,
    pedi_service,
    seeded_staff,
    second_staff,
    seeded_shift,
    cleanup_records,
):
    """Two technicians give the PEDI group two lanes, but one pedicure chair is a
    hard physical limit: the second concurrent pedicure is refused."""
    branch = db_session.get(LocationModel, seeded_branch)
    branch.pedicure_chairs = 1
    db_session.commit()

    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [],
            "capability": {
                str(seeded_staff["staff_id"]): {str(pedi_service): 30},
                str(second_staff.id): {str(pedi_service): 30},
            },
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {"service_id": str(pedi_service), "start_time": seeded_shift["start"].isoformat()}
        ],
    }
    first = client.post("/app/bookings", json=payload, headers=customer_headers)
    assert first.status_code == 201, first.text
    cleanup_records.append(("bookings", first.json()["id"]))
    cleanup_records.append(("booking_details", first.json()["details"][0]["id"]))

    # Group lanes (2) would allow this; the single chair does not.
    second = client.post("/app/bookings", json=payload, headers=other_customer_headers)
    assert second.status_code == 400, second.text

    _cleanup_capabilities(db_session, [seeded_staff["staff_id"], second_staff.id])


def test_resource_cap_zero_means_unlimited(
    client,
    admin_headers,
    customer_headers,
    other_customer_headers,
    db_session,
    seeded_branch,
    pedi_service,
    seeded_staff,
    second_staff,
    seeded_shift,
    cleanup_records,
):
    """A branch left at 0 chairs keeps the old behaviour: only the tech lanes
    bound it, so two techs serve two concurrent pedicures."""
    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [],
            "capability": {
                str(seeded_staff["staff_id"]): {str(pedi_service): 30},
                str(second_staff.id): {str(pedi_service): 30},
            },
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {"service_id": str(pedi_service), "start_time": seeded_shift["start"].isoformat()}
        ],
    }
    first = client.post("/app/bookings", json=payload, headers=customer_headers)
    assert first.status_code == 201, first.text
    cleanup_records.append(("bookings", first.json()["id"]))
    cleanup_records.append(("booking_details", first.json()["details"][0]["id"]))

    second = client.post("/app/bookings", json=payload, headers=other_customer_headers)
    assert second.status_code == 201, second.text
    cleanup_records.append(("bookings", second.json()["id"]))
    cleanup_records.append(("booking_details", second.json()["details"][0]["id"]))

    _cleanup_capabilities(db_session, [seeded_staff["staff_id"], second_staff.id])


def test_branch_resources_roundtrip(client, admin_headers, cleanup_records):
    created = client.post(
        "/app/branches",
        json={"name": f"Res-{uuid.uuid4().hex[:6]}", "pedicure_chairs": 4, "massage_beds": 2},
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    cleanup_records.append(("locations", body["id"]))
    assert body["pedicure_chairs"] == 4
    assert body["manicure_tables"] == 0
    assert body["massage_beds"] == 2

    updated = client.patch(
        f"/app/branches/{body['id']}",
        json={"manicure_tables": 6},
        headers=admin_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["manicure_tables"] == 6
    assert updated.json()["pedicure_chairs"] == 4


# --- Staff leave ------------------------------------------------------------


def test_leave_blocks_materialize_assignment(
    client,
    admin_headers,
    customer_headers,
    db_session,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    """The only capable tech is on leave over the booked time, so the nightly
    allocator leaves the leg unassigned for a human - it never seats them."""
    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [],
            "capability": {str(seeded_staff["staff_id"]): {str(seeded_service): 30}},
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    booking = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {"service_id": str(seeded_service), "start_time": seeded_shift["start"].isoformat()}
            ],
        },
        headers=customer_headers,
    )
    assert booking.status_code == 201, booking.text
    cleanup_records.append(("bookings", booking.json()["id"]))
    cleanup_records.append(("booking_details", booking.json()["details"][0]["id"]))

    leave = client.post(
        "/app/leaves",
        json={
            "staff_id": str(seeded_staff["staff_id"]),
            "start_time": (seeded_shift["start"] - timedelta(hours=1)).isoformat(),
            "end_time": (seeded_shift["start"] + timedelta(hours=3)).isoformat(),
            "reason": "Holiday",
        },
        headers=admin_headers,
    )
    assert leave.status_code == 201, leave.text
    cleanup_records.append(("staff_leaves", leave.json()["id"]))

    day = seeded_shift["start"].astimezone(shop_timezone()).date()
    run = materialize_day(db_session, seeded_branch, day)
    cleanup_records.append(("allocation_runs", run.id))
    assert run.unassigned_count == 1
    assert run.assigned_count == 0

    _cleanup_capabilities(db_session, [seeded_staff["staff_id"]])


def test_leave_reduces_sellable_capacity(
    client,
    admin_headers,
    customer_headers,
    db_session,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    """With one tech, a leave over the slot drops the lane to zero, so that time
    is no longer sellable."""
    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [],
            "capability": {str(seeded_staff["staff_id"]): {str(seeded_service): 30}},
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    leave = client.post(
        "/app/leaves",
        json={
            "staff_id": str(seeded_staff["staff_id"]),
            "start_time": (seeded_shift["start"] - timedelta(hours=1)).isoformat(),
            "end_time": (seeded_shift["start"] + timedelta(hours=3)).isoformat(),
        },
        headers=admin_headers,
    )
    assert leave.status_code == 201, leave.text
    cleanup_records.append(("staff_leaves", leave.json()["id"]))

    blocked = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {"service_id": str(seeded_service), "start_time": seeded_shift["start"].isoformat()}
            ],
        },
        headers=customer_headers,
    )
    assert blocked.status_code == 400, blocked.text

    _cleanup_capabilities(db_session, [seeded_staff["staff_id"]])


def test_full_day_leave_excludes_from_step_a(
    client,
    admin_headers,
    db_session,
    seeded_branch,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    """A tech off for the whole day is never placed by Step A."""
    from sqlalchemy import text

    from app.allocation.infrastructure.assignments import StaffDayAssignmentModel

    day = seeded_shift["start"].astimezone(shop_timezone()).date()
    day_start, day_end = day_bounds_utc(day)

    leave = client.post(
        "/app/leaves",
        json={
            "staff_id": str(seeded_staff["staff_id"]),
            "start_time": day_start.isoformat(),
            "end_time": day_end.isoformat(),
        },
        headers=admin_headers,
    )
    assert leave.status_code == 201, leave.text
    cleanup_records.append(("staff_leaves", leave.json()["id"]))

    try:
        solve_day(db_session, day)
        placed = {
            a.staff_id
            for a in db_session.query(StaffDayAssignmentModel)
            .filter(StaffDayAssignmentModel.day == day)
            .all()
        }
        assert seeded_staff["staff_id"] not in placed
    finally:
        db_session.execute(text("DELETE FROM staff_day_assignments WHERE day = :day"), {"day": day})
        db_session.commit()


def test_leave_endpoints_require_roles(client, customer_headers, seeded_staff):
    forbidden = client.post(
        "/app/leaves",
        json={
            "staff_id": str(seeded_staff["staff_id"]),
            "start_time": "2030-01-02T10:00:00Z",
            "end_time": "2030-01-02T12:00:00Z",
        },
        headers=customer_headers,
    )
    assert forbidden.status_code == 403


# --- Desk schedule management ----------------------------------------------


def test_staff_adds_walk_in_onto_the_grid(
    client,
    staff_headers,
    admin_headers,
    customer_identity,
    db_session,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    """A desk-added appointment with a named tech lands on the day grid straight
    away (no online deposit needed)."""
    add = client.post(
        "/app/schedule/appointments",
        json={
            "branch_id": str(seeded_branch),
            "service_ids": [str(seeded_service)],
            "start_time": seeded_shift["start"].isoformat(),
            "staff_id": str(seeded_staff["staff_id"]),
            "customer_id": str(customer_identity["id"]),
        },
        headers=staff_headers,
    )
    assert add.status_code == 201, add.text
    body = add.json()
    _track_booking(db_session, cleanup_records, body["booking_id"])
    assert body["status"] == "approved"
    assert body["staff_id"] == str(seeded_staff["staff_id"])

    day = seeded_shift["start"].astimezone(shop_timezone()).date()
    schedule = client.get(
        "/app/schedule",
        params={"date": day.isoformat(), "branch_id": str(seeded_branch)},
        headers=admin_headers,
    ).json()
    grid_ids = {a["booking_id"] for a in schedule["appointments"]}
    assert body["booking_id"] in grid_ids


def test_add_appointment_conflict_when_tech_busy(
    client,
    staff_headers,
    customer_identity,
    other_customer_headers,
    db_session,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    payload = {
        "branch_id": str(seeded_branch),
        "service_ids": [str(seeded_service)],
        "start_time": seeded_shift["start"].isoformat(),
        "staff_id": str(seeded_staff["staff_id"]),
        "customer_id": str(customer_identity["id"]),
    }
    first = client.post("/app/schedule/appointments", json=payload, headers=staff_headers)
    assert first.status_code == 201, first.text
    _track_booking(db_session, cleanup_records, first.json()["booking_id"])

    # Same tech, same time -> conflict.
    second = client.post("/app/schedule/appointments", json=payload, headers=staff_headers)
    assert second.status_code == 409, second.text


def test_reschedule_and_delete_appointment(
    client,
    staff_headers,
    customer_identity,
    db_session,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    add = client.post(
        "/app/schedule/appointments",
        json={
            "branch_id": str(seeded_branch),
            "service_ids": [str(seeded_service)],
            "start_time": seeded_shift["start"].isoformat(),
            "staff_id": str(seeded_staff["staff_id"]),
            "customer_id": str(customer_identity["id"]),
        },
        headers=staff_headers,
    )
    assert add.status_code == 201, add.text
    booking_id = add.json()["booking_id"]
    _track_booking(db_session, cleanup_records, booking_id)

    new_start = seeded_shift["start"] + timedelta(hours=2)
    moved = client.patch(
        f"/app/schedule/appointments/{booking_id}",
        json={"new_start_time": new_start.isoformat()},
        headers=staff_headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["start_time"].startswith(new_start.isoformat()[:16])

    deleted = client.delete(f"/app/schedule/appointments/{booking_id}", headers=staff_headers)
    assert deleted.status_code == 200, deleted.text

    booking = db_session.get(BookingModel, uuid.UUID(booking_id))
    db_session.refresh(booking)
    assert booking.status == BookingStatus.CANCELLED


def test_add_appointment_requires_staff_role(
    client, customer_headers, seeded_branch, seeded_service, seeded_shift
):
    forbidden = client.post(
        "/app/schedule/appointments",
        json={
            "branch_id": str(seeded_branch),
            "service_ids": [str(seeded_service)],
            "start_time": seeded_shift["start"].isoformat(),
            "customer_phone": "07000000123",
        },
        headers=customer_headers,
    )
    assert forbidden.status_code == 403
