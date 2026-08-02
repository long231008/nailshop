"""Capability matrix + nightly allocation (design doc v3.2)."""

import uuid

import pytest

from app.allocation.application.materialize import materialize_day
from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.bookings.infrastructure.models import BookingDetailModel
from app.staff.infrastructure.models import StaffModel


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


def _cleanup_capabilities(db_session, staff_ids):
    from sqlalchemy import text

    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(s) for s in staff_ids]},
    )
    db_session.commit()


def test_matrix_roundtrip_and_real_minutes_scheduling(
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
    """Menu says 30' but the named tech's real minutes are 45' - the schedule
    must hold 45' on the tech's timeline, snapped to the 15' grid."""
    matrix = client.get("/app/capability/matrix", headers=admin_headers)
    assert matrix.status_code == 200

    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [],
            "capability": {str(seeded_staff["staff_id"]): {str(seeded_service): 45}},
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    booking = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "staff_id": str(seeded_staff["staff_id"]),
                    "start_time": seeded_shift["start"].isoformat(),
                }
            ],
        },
        headers=customer_headers,
    )
    assert booking.status_code == 201, booking.text
    body = booking.json()
    assert body["details"][0]["duration_min"] == 45
    cleanup_records.append(("bookings", body["id"]))
    cleanup_records.append(("booking_details", body["details"][0]["id"]))

    _cleanup_capabilities(db_session, [seeded_staff["staff_id"]])


def test_matrix_rejects_a_typo_beyond_3x_menu(
    client, admin_headers, seeded_service, seeded_staff
):
    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [],
            # Menu is 30' - 120' is beyond 3x and looks like a typo.
            "capability": {str(seeded_staff["staff_id"]): {str(seeded_service): 120}},
        },
        headers=admin_headers,
    )
    assert save.status_code == 422


def test_any_tech_booking_is_materialized_with_shrunk_minutes(
    client,
    admin_headers,
    customer_headers,
    db_session,
    seeded_branch,
    seeded_service,
    seeded_staff,
    second_staff,
    seeded_shift,
    cleanup_records,
):
    """Any-tech holds the slowest tech's minutes (60'); materialize picks a tech
    and shrinks the leg to that tech's real minutes."""
    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [],
            "capability": {
                str(seeded_staff["staff_id"]): {str(seeded_service): 30},
                str(second_staff.id): {str(seeded_service): 60},
            },
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    booking = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "start_time": seeded_shift["start"].isoformat(),
                }
            ],
        },
        headers=customer_headers,
    )
    assert booking.status_code == 201, booking.text
    body = booking.json()
    # Cautious hold: the slowest eligible tech (60').
    assert body["details"][0]["duration_min"] == 60
    assert body["details"][0]["staff_id"] is None
    cleanup_records.append(("bookings", body["id"]))
    cleanup_records.append(("booking_details", body["details"][0]["id"]))

    run = materialize_day(db_session, seeded_branch, seeded_shift["start"].date())
    cleanup_records.append(("allocation_runs", run.id))
    assert run.assigned_count == 1
    assert run.unassigned_count == 0

    detail = db_session.get(BookingDetailModel, uuid.UUID(body["details"][0]["id"]))
    db_session.refresh(detail)
    assert detail.staff_id is not None
    # Fairness aside, the leg must have shrunk to the assigned tech's minutes.
    minutes = int((detail.end_time - detail.start_time).total_seconds() // 60)
    assert minutes in (30, 60)

    _cleanup_capabilities(db_session, [seeded_staff["staff_id"], second_staff.id])


def test_allocation_endpoints_require_admin(client, customer_headers):
    status = client.get(
        "/app/allocation/status",
        params={"target_date": "2030-01-02"},
        headers=customer_headers,
    )
    assert status.status_code == 403


def test_capacity_blocks_overselling_one_tech_branch(
    client,
    admin_headers,
    customer_headers,
    other_customer_headers,
    db_session,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    """One tech = one lane: two any-tech bookings at the same instant must not
    both go through (counting overlapping lanes, not total minutes - fix #1)."""
    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [],
            "capability": {str(seeded_staff["staff_id"]): {str(seeded_service): 30}},
        },
        headers=admin_headers,
    )
    assert save.status_code == 200

    payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "start_time": seeded_shift["start"].isoformat(),
            }
        ],
    }
    first = client.post("/app/bookings", json=payload, headers=customer_headers)
    assert first.status_code == 201, first.text
    cleanup_records.append(("bookings", first.json()["id"]))
    cleanup_records.append(("booking_details", first.json()["details"][0]["id"]))

    second = client.post("/app/bookings", json=payload, headers=other_customer_headers)
    assert second.status_code == 400
    assert "no longer has room" in second.json()["detail"]

    _cleanup_capabilities(db_session, [seeded_staff["staff_id"]])


# --- Chain staff: Step A placement ------------------------------------


def test_step_a_places_every_available_tech(
    db_session,
    seeded_branch,
    seeded_staff,
    second_staff,
    seeded_shift,
):
    from sqlalchemy import text

    from app.allocation.application.roster import solve_day
    from app.allocation.infrastructure.assignments import (
        AssignmentSource,
        StaffDayAssignmentModel,
    )

    day = seeded_shift["start"].date()

    try:
        solve_day(db_session, day)

        rows = {
            a.staff_id: a
            for a in db_session.query(StaffDayAssignmentModel)
            .filter(StaffDayAssignmentModel.day == day)
            .all()
        }
        # Both techs got a branch for the day, placed by the solver.
        assert rows[seeded_staff["staff_id"]].source == AssignmentSource.AUTO
        assert rows[second_staff.id].source == AssignmentSource.AUTO
    finally:
        # solve_day touches every active tech in the dev database - remove the
        # whole day's assignments so no side effects outlive the test.
        db_session.execute(
            text("DELETE FROM staff_day_assignments WHERE day = :day"), {"day": day}
        )
        db_session.commit()


def test_preference_is_ignored_by_allocator_and_granted_by_hand(
    client,
    admin_headers,
    customer_headers,
    other_customer_headers,
    db_session,
    seeded_branch,
    seeded_service,
    seeded_staff,
    second_staff,
    seeded_shift,
    cleanup_records,
):
    """The allocator optimises freely (fairness wins, the wish is ignored);
    the wish shows up on /allocation/status and a manager grants it by hand
    via /allocation/reassign."""
    from app.allocation.application.materialize import materialize_day
    from app.shared.infrastructure.clock import shop_timezone

    first = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "start_time": seeded_shift["start"].isoformat(),
                }
            ],
        },
        headers=customer_headers,
    )
    assert first.status_code == 201, first.text
    cleanup_records.append(("bookings", first.json()["id"]))
    cleanup_records.append(("booking_details", first.json()["details"][0]["id"]))

    second = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "staff_id": str(seeded_staff["staff_id"]),
                    "start_time": (seeded_shift["start"].replace(hour=13)).isoformat(),
                }
            ],
        },
        headers=other_customer_headers,
    )
    assert second.status_code == 201, second.text
    cleanup_records.append(("bookings", second.json()["id"]))
    detail_id = second.json()["details"][0]["id"]
    cleanup_records.append(("booking_details", detail_id))

    day = seeded_shift["start"].astimezone(shop_timezone()).date()
    run = materialize_day(db_session, seeded_branch, day)
    cleanup_records.append(("allocation_runs", run.id))
    assert run.unassigned_count == 0

    # Fairness wins: the wished (busier) tech does NOT get the leg.
    detail = db_session.get(BookingDetailModel, uuid.UUID(detail_id))
    db_session.refresh(detail)
    assert detail.staff_id == second_staff.id

    # The wish is surfaced for a human next to the machine's decision.
    status_body = client.get(
        "/app/allocation/status",
        params={"target_date": day.isoformat(), "branch_id": str(seeded_branch)},
        headers=admin_headers,
    ).json()
    entry = next(
        p for p in status_body["preferences"] if p["booking_detail_id"] == detail_id
    )
    assert entry["preferred_staff_id"] == str(seeded_staff["staff_id"])
    assert entry["assigned_staff_id"] == str(second_staff.id)
    assert entry["honoured"] is False

    # The manager grants the wish by hand; the leg moves and is audited.
    reassign = client.post(
        "/app/allocation/reassign",
        json={"booking_detail_id": detail_id, "staff_id": str(seeded_staff["staff_id"])},
        headers=admin_headers,
    )
    assert reassign.status_code == 200, reassign.text
    db_session.refresh(detail)
    assert detail.staff_id == seeded_staff["staff_id"]

    from app.audit_log.infrastructure.models import AuditLogModel

    log_entry = (
        db_session.query(AuditLogModel)
        .filter(
            AuditLogModel.action == "allocation.reassigned",
            AuditLogModel.entity_id == uuid.UUID(second.json()["id"]),
        )
        .first()
    )
    cleanup_records.append(("audit_logs", log_entry.id))
    assert log_entry.details["to_staff_id"] == str(seeded_staff["staff_id"])

    # The tool is general, not wish-only: the manager can also move the other
    # leg onto the now-idle tech to rebalance the day.
    rebalance = client.post(
        "/app/allocation/reassign",
        json={
            "booking_detail_id": first.json()["details"][0]["id"],
            "staff_id": str(second_staff.id),
        },
        headers=admin_headers,
    )
    assert rebalance.status_code == 200, rebalance.text


def test_matrix_save_replaces_only_staff_in_payload(
    client,
    admin_headers,
    db_session,
    seeded_service,
    seeded_staff,
    second_staff,
):
    """A partial payload edits only the technicians it names; everyone else's
    cells survive. An empty mapping clears a technician's whole row."""
    first_id = str(seeded_staff["staff_id"])
    second_id = str(second_staff.id)
    service_id = str(seeded_service)

    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [],
            "capability": {first_id: {service_id: 45}, second_id: {service_id: 60}},
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    partial = client.put(
        "/app/capability/matrix",
        json={"services": [], "staff": [], "capability": {first_id: {service_id: 30}}},
        headers=admin_headers,
    )
    assert partial.status_code == 200, partial.text

    matrix = client.get("/app/capability/matrix", headers=admin_headers).json()["capability"]
    assert matrix[first_id][service_id] == 30
    # The second tech was absent from the payload - their row must survive.
    assert matrix[second_id][service_id] == 60

    cleared = client.put(
        "/app/capability/matrix",
        json={"services": [], "staff": [], "capability": {second_id: {}}},
        headers=admin_headers,
    )
    assert cleared.status_code == 200, cleared.text
    matrix = client.get("/app/capability/matrix", headers=admin_headers).json()["capability"]
    assert not matrix.get(second_id)
    assert matrix[first_id][service_id] == 30

    _cleanup_capabilities(db_session, [seeded_staff["staff_id"], second_staff.id])

