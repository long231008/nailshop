"""The desk's two new views: what it can still sell, and who works what.

Slot suggestions are the website's search without the customer booking window -
a walk-in this afternoon or a phone call after tonight's close is exactly the
booking the window exists to stop the public making, and exactly the one the
desk takes all day. Day sheets are the point of the 21:00 run: one list per
technician, theirs alone unless you are the admin.
"""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.allocation.application.materialize import materialize_day
from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import shop_timezone, today_in_shop_tz
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
    staff = StaffModel(user_id=user.id, branch_id=seeded_branch, display_name="Other Tech")
    db_session.add(staff)
    db_session.commit()
    cleanup_records.append(("users", user.id))
    cleanup_records.append(("staff", staff.id))
    return staff


def test_desk_can_be_offered_times_the_website_is_shut_out_of(
    client, staff_headers, seeded_service, seeded_staff, seeded_shift
):
    """Today is past the customer window - the website refuses it outright. The
    desk still has to be able to book this afternoon's walk-in."""
    today = today_in_shop_tz()

    public = client.get(
        "/app/availability",
        params={
            "branch_id": str(seeded_staff["branch_id"]),
            "service_id": str(seeded_service),
            "date": today.isoformat(),
        },
    )
    assert public.status_code == 200
    assert public.json()["window"] != "open", "the customer window should be shut for today"

    desk = client.get(
        "/app/schedule/slots",
        params={
            "branch_id": str(seeded_staff["branch_id"]),
            "date": today.isoformat(),
            "service_ids": str(seeded_service),
        },
        headers=staff_headers,
    )
    assert desk.status_code == 200, desk.text
    assert isinstance(desk.json()["slots"], list)


def test_desk_slots_carry_the_same_star_the_website_shows(
    client, staff_headers, seeded_service, seeded_staff, seeded_shift
):
    day = seeded_shift["start"].astimezone(shop_timezone()).date()

    desk = client.get(
        "/app/schedule/slots",
        params={
            "branch_id": str(seeded_staff["branch_id"]),
            "date": day.isoformat(),
            "service_ids": str(seeded_service),
        },
        headers=staff_headers,
    )
    assert desk.status_code == 200, desk.text
    slots = desk.json()["slots"]
    assert len(slots) > 0
    assert any(slot["recommended"] for slot in slots), "opening time is always a tidy start"


def test_desk_slots_need_staff_rights(client, customer_headers, seeded_service, seeded_staff):
    response = client.get(
        "/app/schedule/slots",
        params={
            "branch_id": str(seeded_staff["branch_id"]),
            "date": today_in_shop_tz().isoformat(),
            "service_ids": str(seeded_service),
        },
        headers=customer_headers,
    )
    assert response.status_code == 403


def test_admin_sees_a_sheet_per_technician_and_staff_only_their_own(
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
    service = ServiceModel(
        branch_id=seeded_branch,
        name="Sheet Service",
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
            "staff": [],
            "capability": {
                str(seeded_staff["staff_id"]): {str(service.id): 30},
                str(second_staff.id): {str(service.id): 30},
            },
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    # Two customers at the same time, so the nightly run has to use both techs.
    start = seeded_shift["start"]
    for headers in (customer_headers, other_customer_headers):
        response = client.post(
            "/app/bookings",
            json={
                "branch_id": str(seeded_branch),
                "items": [{"service_id": str(service.id), "start_time": start.isoformat()}],
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        cleanup_records.append(("bookings", response.json()["id"]))
        cleanup_records.append(("booking_details", response.json()["details"][0]["id"]))

    day = start.astimezone(shop_timezone()).date()
    run = materialize_day(db_session, seeded_branch, day)
    cleanup_records.append(("allocation_runs", run.id))
    assert run.unassigned_count == 0

    as_admin = client.get(
        "/app/schedule/day-sheets",
        params={"date": day.isoformat(), "branch_id": str(seeded_branch)},
        headers=admin_headers,
    )
    assert as_admin.status_code == 200, as_admin.text
    body = as_admin.json()
    assert body["allocated"] is True
    sheets = {sheet["staff_name"]: sheet for sheet in body["sheets"]}
    assert {"Test Staff", "Other Tech"} <= set(sheets)
    # One customer each, on their own sheet - not one shared list.
    assert sheets["Test Staff"]["appointment_count"] == 1
    assert sheets["Other Tech"]["appointment_count"] == 1
    assert sheets["Test Staff"]["working_minutes"] == 30
    assert sheets["Test Staff"]["appointments"][0]["customer_phone"]

    as_staff = client.get(
        "/app/schedule/day-sheets",
        params={"date": day.isoformat(), "branch_id": str(seeded_branch)},
        headers=seeded_staff["headers"],
    )
    assert as_staff.status_code == 200, as_staff.text
    mine = as_staff.json()["sheets"]
    assert len(mine) == 1
    assert mine[0]["staff_id"] == str(seeded_staff["staff_id"])
    # The other technician's customer must not be reachable from here.
    theirs = sheets["Other Tech"]["appointments"][0]["customer_phone"]
    assert all(a["customer_phone"] != theirs for a in mine[0]["appointments"])

    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(seeded_staff["staff_id"]), str(second_staff.id)]},
    )
    db_session.commit()


def test_day_sheets_before_the_nightly_run_say_so(
    client, admin_headers, seeded_branch, seeded_staff, seeded_shift
):
    """A day nobody has closed yet has no named technicians, so the sheets are
    empty of work - the flag is what tells the office why."""
    day = today_in_shop_tz() + timedelta(days=5)
    response = client.get(
        "/app/schedule/day-sheets",
        params={"date": day.isoformat(), "branch_id": str(seeded_branch)},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["allocated"] is False
