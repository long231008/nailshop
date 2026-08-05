"""Selling has to respect the weekly ceiling the nightly run enforces.

max_hours_week is a real limit at assignment time - a technician past it is
skipped - but selling never knew it existed, so the salon could take four
bookings one technician has hours for two of and only find out at 21:00, with
two customers holding appointments nobody can work.

Owed work cannot be charged to a person yet: a leg with no technician has not
been given to one. So the sale checks a pool - all the work the week has taken
on against all the hours the team has left. Weaker than the per-person test the
nightly run does, but true, and it is what selling can know.
"""

import uuid

from sqlalchemy import text

from app.allocation.application.materialize import materialize_day
from app.bookings.infrastructure.models import BookingDetailModel
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import shop_timezone
from app.staff.infrastructure.models import StaffModel


def test_the_week_stops_selling_once_the_team_is_out_of_hours(
    client,
    admin_headers,
    customer_headers,
    other_customer_headers,
    db_session,
    seeded_branch,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    """One technician with an hour left in the week and a 30-minute service:
    two bookings fit exactly, the third must be refused rather than left for a
    human at 21:00."""
    service = ServiceModel(
        branch_id=seeded_branch,
        name="Half Hour",
        category="gel",
        duration_min=30,
        base_price=20.0,
        skill_group="MANI",
    )
    db_session.add(service)
    db_session.commit()
    cleanup_records.append(("services", service.id))

    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [
                {
                    "id": str(seeded_staff["staff_id"]),
                    "days_off": "",
                    "max_hours_week": 1,
                }
            ],
            "capability": {str(seeded_staff["staff_id"]): {str(service.id): 30}},
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text
    assert db_session.get(StaffModel, seeded_staff["staff_id"]).max_hours_week == 1

    day = seeded_shift["start"].astimezone(shop_timezone()).date()
    legs = []

    def book(hour, headers):
        start = seeded_shift["start"].replace(hour=hour, minute=0)
        return client.post(
            "/app/bookings",
            json={
                "branch_id": str(seeded_branch),
                "items": [{"service_id": str(service.id), "start_time": start.isoformat()}],
            },
            headers=headers,
        )

    for hour, headers in ((9, customer_headers), (11, other_customer_headers)):
        response = book(hour, headers)
        assert response.status_code == 201, response.text
        cleanup_records.append(("bookings", response.json()["id"]))
        legs.append(uuid.UUID(response.json()["details"][0]["id"]))
        cleanup_records.append(("booking_details", legs[-1]))

    # The hour is spent. A third booking has nobody left to do it.
    third = book(13, customer_headers)
    if third.status_code == 201:  # track it so a failure here cannot strand rows
        cleanup_records.append(("bookings", third.json()["id"]))
        cleanup_records.append(("booking_details", third.json()["details"][0]["id"]))
    assert third.status_code == 400, third.text

    # And what was sold is all workable: nothing left for a human.
    run = materialize_day(db_session, seeded_branch, day)
    cleanup_records.append(("allocation_runs", run.id))
    assert run.unassigned_count == 0
    assert run.assigned_count == 2

    db_session.expire_all()
    for leg in legs:
        assert db_session.get(BookingDetailModel, leg).staff_id == seeded_staff["staff_id"]

    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = :i"),
        {"i": seeded_staff["staff_id"]},
    )
    db_session.commit()


def test_the_desk_cannot_book_a_named_technician_past_their_week(
    client,
    admin_headers,
    staff_headers,
    db_session,
    seeded_branch,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    """Naming the technician skips the pool check - the ceiling is then that one
    person's, so it is tested against them directly."""
    service = ServiceModel(
        branch_id=seeded_branch,
        name="Desk Half Hour",
        category="gel",
        duration_min=30,
        base_price=20.0,
        skill_group="MANI",
    )
    db_session.add(service)
    db_session.commit()
    cleanup_records.append(("services", service.id))

    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [{"id": str(seeded_staff["staff_id"]), "days_off": "", "max_hours_week": 0}],
            "capability": {str(seeded_staff["staff_id"]): {str(service.id): 30}},
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    start = seeded_shift["start"].replace(hour=10, minute=0)
    response = client.post(
        "/app/schedule/appointments",
        json={
            "branch_id": str(seeded_branch),
            "service_ids": [str(service.id)],
            "start_time": start.isoformat(),
            "staff_id": str(seeded_staff["staff_id"]),
            "customer_phone": "07700900456",
            "customer_name": "Walk In",
        },
        headers=staff_headers,
    )
    assert response.status_code == 400, response.text
    assert "no hours left that week" in response.json()["detail"]

    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = :i"),
        {"i": seeded_staff["staff_id"]},
    )
    db_session.execute(text("DELETE FROM users WHERE phone_number = '07700900456'"))
    db_session.commit()
