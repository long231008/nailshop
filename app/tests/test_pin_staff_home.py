"""Pinning a technician to one salon, from the admin matrix screen.

The engine has always honoured it - Step A leaves a non-floating technician
at their home branch and selling counts them there - but nothing exposed the
two columns. The matrix save now carries them, as a pair and only when sent,
so an older payload that just edits hours can never silently unpin anyone.
"""

from datetime import timedelta

import uuid

from app.allocation.application.roster import solve_day
from app.allocation.infrastructure.assignments import StaffDayAssignmentModel
from app.branches.infrastructure.models import LocationModel
from app.shared.infrastructure.clock import today_in_shop_tz


def _second_branch(db_session, cleanup_records):
    branch = LocationModel(name=f"Pin-{uuid.uuid4().hex[:6]}", address="2 Test St")
    db_session.add(branch)
    db_session.commit()
    cleanup_records.append(("locations", branch.id))
    return branch.id


def _staff_of(client, admin_headers, staff_id):
    response = client.get("/app/capability/matrix", headers=admin_headers)
    assert response.status_code == 200, response.text
    return next(t for t in response.json()["staff"] if t["id"] == str(staff_id))


def _send_home(db_session, staff_id, branch_id):
    """Point the technician back at the seeded branch so cleanup can drop the
    pin branch - staff.branch_id holds a foreign key to it."""
    from app.staff.infrastructure.models import StaffModel

    staff = db_session.get(StaffModel, staff_id)
    staff.branch_id = branch_id
    staff.floating = True
    db_session.commit()


def test_a_pinned_technician_stays_at_that_salon_every_day(
    client, admin_headers, db_session, seeded_branch, seeded_staff, cleanup_records
):
    """Home is salon A and round-robin would also say A - pinning to salon B
    must show in the matrix and put Step A's day placement at B."""
    other = _second_branch(db_session, cleanup_records)
    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [{"id": str(seeded_staff["staff_id"]), "branch_id": str(other), "floating": False}],
            "capability": {},
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    shown = _staff_of(client, admin_headers, seeded_staff["staff_id"])
    assert shown["branch_id"] == str(other)
    assert shown["floating"] is False

    day = today_in_shop_tz() + timedelta(days=30)
    try:
        solve_day(db_session, day)
        db_session.commit()
        placed = (
            db_session.query(StaffDayAssignmentModel)
            .filter_by(staff_id=seeded_staff["staff_id"], day=day)
            .one()
        )
        assert placed.branch_id == other, "the pin must beat home and round-robin"
    finally:
        db_session.query(StaffDayAssignmentModel).filter_by(day=day).delete()
        db_session.commit()
        _send_home(db_session, seeded_staff["staff_id"], seeded_branch)


def test_a_pin_without_a_home_salon_is_refused(
    client, admin_headers, seeded_staff
):
    response = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [{"id": str(seeded_staff["staff_id"]), "floating": False}],
            "capability": {},
        },
        headers=admin_headers,
    )
    assert response.status_code == 422, response.text


def test_a_pin_to_an_unknown_branch_is_refused(
    client, admin_headers, seeded_staff
):
    response = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [
                {
                    "id": str(seeded_staff["staff_id"]),
                    "branch_id": str(uuid.uuid4()),
                    "floating": False,
                }
            ],
            "capability": {},
        },
        headers=admin_headers,
    )
    assert response.status_code == 404, response.text


def test_a_save_that_never_mentions_placement_leaves_the_pin_alone(
    client, admin_headers, db_session, seeded_branch, seeded_staff, cleanup_records
):
    """The hours screen predates these fields - its payload must not unpin."""
    other = _second_branch(db_session, cleanup_records)
    pin = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [{"id": str(seeded_staff["staff_id"]), "branch_id": str(other), "floating": False}],
            "capability": {},
        },
        headers=admin_headers,
    )
    assert pin.status_code == 200, pin.text

    hours_only = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [{"id": str(seeded_staff["staff_id"]), "max_hours_week": 30}],
            "capability": {},
        },
        headers=admin_headers,
    )
    assert hours_only.status_code == 200, hours_only.text

    shown = _staff_of(client, admin_headers, seeded_staff["staff_id"])
    assert shown["branch_id"] == str(other)
    assert shown["floating"] is False
    assert shown["max_hours_week"] == 30

    _send_home(db_session, seeded_staff["staff_id"], seeded_branch)
