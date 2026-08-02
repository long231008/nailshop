"""The admin re-run endpoint, the nightly entry point, and reassign's refusals."""

import uuid

import pytest
from sqlalchemy import text

from app.allocation.application.nightly import run_nightly_allocation
from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.bookings.infrastructure.models import BookingDetailModel
from app.shared.infrastructure.clock import shop_timezone
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


def _wipe_day_assignments(db_session, day):
    # solve_day places every active tech in the shared dev database; remove the
    # whole day's rows so nothing outlives the test.
    db_session.execute(text("DELETE FROM staff_day_assignments WHERE day = :day"), {"day": day})
    db_session.commit()


def _any_tech_booking(client, customer_headers, seeded_branch, seeded_service, start):
    return client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [{"service_id": str(seeded_service), "start_time": start.isoformat()}],
        },
        headers=customer_headers,
    )


def test_allocation_run_endpoint_assigns_the_day(
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
    booking = _any_tech_booking(
        client, customer_headers, seeded_branch, seeded_service, seeded_shift["start"]
    )
    assert booking.status_code == 201, booking.text
    detail_id = booking.json()["details"][0]["id"]
    cleanup_records.append(("bookings", booking.json()["id"]))
    cleanup_records.append(("booking_details", detail_id))

    day = seeded_shift["start"].astimezone(shop_timezone()).date()
    try:
        response = client.post(
            "/app/allocation/run",
            json={"target_date": day.isoformat(), "branch_id": str(seeded_branch)},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        runs = response.json()
        assert len(runs) == 1
        for run in runs:
            cleanup_records.append(("allocation_runs", run["id"]))
        assert runs[0]["assigned_count"] >= 1
        assert runs[0]["unassigned_count"] == 0

        detail = db_session.get(BookingDetailModel, uuid.UUID(detail_id))
        db_session.refresh(detail)
        assert detail.staff_id is not None
    finally:
        _wipe_day_assignments(db_session, day)


def test_allocation_run_release_hands_a_sick_techs_legs_to_another(
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
    booking = _any_tech_booking(
        client, customer_headers, seeded_branch, seeded_service, seeded_shift["start"]
    )
    assert booking.status_code == 201, booking.text
    detail_id = booking.json()["details"][0]["id"]
    cleanup_records.append(("bookings", booking.json()["id"]))
    cleanup_records.append(("booking_details", detail_id))

    day = seeded_shift["start"].astimezone(shop_timezone()).date()
    staff_row = db_session.get(StaffModel, seeded_staff["staff_id"])
    try:
        first = client.post(
            "/app/allocation/run",
            json={"target_date": day.isoformat(), "branch_id": str(seeded_branch)},
            headers=admin_headers,
        )
        assert first.status_code == 200, first.text
        for run in first.json():
            cleanup_records.append(("allocation_runs", run["id"]))

        detail = db_session.get(BookingDetailModel, uuid.UUID(detail_id))
        db_session.refresh(detail)
        assigned_first = detail.staff_id
        assert assigned_first is not None

        # The assigned tech calls in sick for that weekday; release + re-run.
        sick = db_session.get(StaffModel, assigned_first)
        sick.days_off = str(day.weekday())
        db_session.commit()
        _wipe_day_assignments(db_session, day)

        second = client.post(
            "/app/allocation/run",
            json={
                "target_date": day.isoformat(),
                "branch_id": str(seeded_branch),
                "release_staff_id": str(assigned_first),
            },
            headers=admin_headers,
        )
        assert second.status_code == 200, second.text
        for run in second.json():
            cleanup_records.append(("allocation_runs", run["id"]))

        db_session.refresh(detail)
        assert detail.staff_id is not None
        assert detail.staff_id != assigned_first
    finally:
        staff_row.days_off = ""
        other = db_session.get(StaffModel, second_staff.id)
        other.days_off = ""
        db_session.commit()
        _wipe_day_assignments(db_session, day)


def test_nightly_allocation_skips_closed_days(monkeypatch, db_session):
    import app.allocation.application.nightly as nightly_module

    monkeypatch.setattr(nightly_module, "is_closed_day", lambda day: True)
    assert run_nightly_allocation(db_session) == []


def test_reassign_refusals(
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
    from app.branches.infrastructure.models import LocationModel

    booking = _any_tech_booking(
        client, customer_headers, seeded_branch, seeded_service, seeded_shift["start"]
    )
    assert booking.status_code == 201, booking.text
    detail_id = booking.json()["details"][0]["id"]
    cleanup_records.append(("bookings", booking.json()["id"]))
    cleanup_records.append(("booking_details", detail_id))

    day = seeded_shift["start"].astimezone(shop_timezone()).date()

    # 404: unknown leg.
    missing = client.post(
        "/app/allocation/reassign",
        json={"booking_detail_id": str(uuid.uuid4()), "staff_id": str(second_staff.id)},
        headers=admin_headers,
    )
    assert missing.status_code == 404

    # 400: the tech does not work that day.
    off = db_session.get(StaffModel, second_staff.id)
    off.days_off = str(day.weekday())
    db_session.commit()
    not_working = client.post(
        "/app/allocation/reassign",
        json={"booking_detail_id": detail_id, "staff_id": str(second_staff.id)},
        headers=admin_headers,
    )
    assert not_working.status_code == 400
    assert "not working" in not_working.json()["detail"]
    off.days_off = ""
    db_session.commit()

    # 400: the tech stands at another branch that day.
    elsewhere = LocationModel(name=f"Elsewhere {uuid.uuid4().hex[:6]}")
    db_session.add(elsewhere)
    db_session.flush()
    stranger_user = UserModel(
        phone_number=f"09{uuid.uuid4().int % 10**8:08d}",
        status=UserStatus.ACTIVE,
        role=UserRole.STAFF,
    )
    db_session.add(stranger_user)
    db_session.flush()
    stranger = StaffModel(
        user_id=stranger_user.id, branch_id=elsewhere.id, display_name="Elsewhere Tech"
    )
    db_session.add(stranger)
    db_session.commit()
    cleanup_records.append(("locations", elsewhere.id))
    cleanup_records.append(("users", stranger_user.id))
    cleanup_records.append(("staff", stranger.id))

    wrong_branch = client.post(
        "/app/allocation/reassign",
        json={"booking_detail_id": detail_id, "staff_id": str(stranger.id)},
        headers=admin_headers,
    )
    assert wrong_branch.status_code == 400
    assert "not at this branch" in wrong_branch.json()["detail"]

    # 400: the matrix is configured and the tech lacks the cell.
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
    try:
        no_cell = client.post(
            "/app/allocation/reassign",
            json={"booking_detail_id": detail_id, "staff_id": str(second_staff.id)},
            headers=admin_headers,
        )
        assert no_cell.status_code == 400
        assert "does not offer" in no_cell.json()["detail"]
    finally:
        db_session.execute(
            text("DELETE FROM staff_capabilities WHERE staff_id = :sid"),
            {"sid": str(seeded_staff["staff_id"])},
        )
        db_session.commit()

    # 409: the target tech is already busy at that moment.
    clash = _any_tech_booking(
        client, customer_headers, seeded_branch, seeded_service, seeded_shift["start"]
    )
    assert clash.status_code == 201, clash.text
    clash_detail = clash.json()["details"][0]["id"]
    cleanup_records.append(("bookings", clash.json()["id"]))
    cleanup_records.append(("booking_details", clash_detail))

    from app.allocation.application.materialize import materialize_day

    try:
        run = materialize_day(db_session, seeded_branch, day)
        cleanup_records.append(("allocation_runs", run.id))
        first_leg = db_session.get(BookingDetailModel, uuid.UUID(detail_id))
        second_leg = db_session.get(BookingDetailModel, uuid.UUID(clash_detail))
        db_session.refresh(first_leg)
        db_session.refresh(second_leg)
        assert {first_leg.staff_id, second_leg.staff_id} == {
            seeded_staff["staff_id"],
            second_staff.id,
        }

        conflict = client.post(
            "/app/allocation/reassign",
            json={"booking_detail_id": detail_id, "staff_id": str(second_leg.staff_id)},
            headers=admin_headers,
        )
        assert conflict.status_code == 409
        assert "not free" in conflict.json()["detail"]
    finally:
        _wipe_day_assignments(db_session, day)
