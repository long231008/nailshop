"""A part-timer is only in for part of the day, and the whole chain has to know.

Full time needs nothing set - the technician follows the shop's opening hours,
so moving those moves everyone. Give someone their own hours and every layer
narrows with them: the website stops offering times they cannot cover, the
nightly run will not seat them outside the window, and neither will a manager
by hand.
"""

import uuid
from datetime import datetime, time, timedelta, timezone

import pytest
from sqlalchemy import text

from app.allocation.application.materialize import materialize_day
from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.bookings.infrastructure.models import BookingDetailModel
from app.capability.application.matrix import working_window
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import shop_timezone
from app.staff.infrastructure.models import StaffModel


def _service(db_session, cleanup_records, branch_id, name, minutes=30):
    service = ServiceModel(
        branch_id=branch_id,
        name=name,
        category="gel",
        duration_min=minutes,
        base_price=20.0,
        skill_group="MANI",
    )
    db_session.add(service)
    db_session.commit()
    cleanup_records.append(("services", service.id))
    return service


@pytest.fixture
def part_timer(db_session, cleanup_records, seeded_branch):
    user = UserModel(
        phone_number=f"09{uuid.uuid4().int % 10**8:08d}",
        status=UserStatus.ACTIVE,
        role=UserRole.STAFF,
    )
    db_session.add(user)
    db_session.flush()
    staff = StaffModel(user_id=user.id, branch_id=seeded_branch, display_name="Part Timer")
    db_session.add(staff)
    db_session.commit()
    cleanup_records.append(("users", user.id))
    cleanup_records.append(("staff", staff.id))
    return staff


def test_full_time_follows_the_shop_and_part_time_narrows_it(db_session, seeded_staff, part_timer):
    day = datetime.now(shop_timezone()).date() + timedelta(days=2)
    full = db_session.get(StaffModel, seeded_staff["staff_id"])

    tz = shop_timezone()
    full_start, full_end = working_window(full, day)
    assert full_start.astimezone(tz).hour == 9
    # Not clipped at closing: the shop lets a visit start at the last booking
    # time and run past 18:00, and a full-timer stays to finish it. Closing is
    # enforced on when a visit may start, not on when this window ends.
    close = datetime.combine(day, time(hour=18), tzinfo=tz).astimezone(timezone.utc)
    assert full_end > close

    part_timer.work_start_hour = 13
    part_timer.work_end_hour = 17
    db_session.commit()
    start, end = working_window(part_timer, day)
    assert start.astimezone(tz).hour == 13
    assert end.astimezone(tz).hour == 17

    # A start before opening is clamped - nobody works before the door opens.
    part_timer.work_start_hour = 6
    part_timer.work_end_hour = None
    db_session.commit()
    start, end = working_window(part_timer, day)
    assert (start, end) == (full_start, full_end)


def test_the_website_stops_selling_once_the_only_tech_has_gone_home(
    client,
    admin_headers,
    db_session,
    seeded_branch,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    """The one technician who can do this service leaves at 13:00. Times after
    that cannot be staffed, so they must not be offered."""
    service = _service(db_session, cleanup_records, seeded_branch, "Morning Only")
    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [
                {
                    "id": str(seeded_staff["staff_id"]),
                    "days_off": "",
                    "work_start_hour": 9,
                    "work_end_hour": 13,
                    "max_hours_week": 40,
                }
            ],
            "capability": {str(seeded_staff["staff_id"]): {str(service.id): 30}},
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    day = seeded_shift["start"].astimezone(shop_timezone()).date()
    response = client.get(
        "/app/availability",
        params={
            "branch_id": str(seeded_branch),
            "service_id": str(service.id),
            "date": day.isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    slots = response.json()["slots"]
    assert len(slots) > 0, "the morning is still sellable"
    latest = max(datetime.fromisoformat(s["end_time"]) for s in slots)
    assert latest <= datetime.combine(day, time(hour=13), tzinfo=shop_timezone()).astimezone(
        timezone.utc
    ), "sold a time the only capable technician is not in for"

    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = :i"),
        {"i": seeded_staff["staff_id"]},
    )
    db_session.commit()


def test_the_nightly_run_will_not_seat_a_part_timer_outside_their_hours(
    client,
    admin_headers,
    customer_headers,
    db_session,
    seeded_branch,
    seeded_staff,
    part_timer,
    seeded_shift,
    cleanup_records,
):
    """Two technicians, both able. One is in all day, one only in the morning,
    and the booking is in the afternoon - so it can only go one way."""
    service = _service(db_session, cleanup_records, seeded_branch, "Either Tech")
    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [
                {
                    "id": str(part_timer.id),
                    "days_off": "",
                    "work_start_hour": 9,
                    "work_end_hour": 12,
                    "max_hours_week": 40,
                }
            ],
            "capability": {
                str(seeded_staff["staff_id"]): {str(service.id): 30},
                str(part_timer.id): {str(service.id): 30},
            },
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    day = seeded_shift["start"].astimezone(shop_timezone()).date()
    start = datetime.combine(day, time(hour=15), tzinfo=shop_timezone()).astimezone(timezone.utc)
    booking = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [{"service_id": str(service.id), "start_time": start.isoformat()}],
        },
        headers=customer_headers,
    )
    assert booking.status_code == 201, booking.text
    cleanup_records.append(("bookings", booking.json()["id"]))
    leg_id = uuid.UUID(booking.json()["details"][0]["id"])
    cleanup_records.append(("booking_details", leg_id))

    run = materialize_day(db_session, seeded_branch, day)
    cleanup_records.append(("allocation_runs", run.id))
    assert run.unassigned_count == 0

    db_session.expire_all()
    assert db_session.get(BookingDetailModel, leg_id).staff_id == seeded_staff["staff_id"], (
        "the part-timer had gone home at 12:00"
    )

    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(seeded_staff["staff_id"]), str(part_timer.id)]},
    )
    db_session.commit()


def test_the_desk_cannot_name_a_technician_who_has_gone_home(
    client,
    admin_headers,
    staff_headers,
    db_session,
    seeded_branch,
    part_timer,
    seeded_shift,
    cleanup_records,
):
    service = _service(db_session, cleanup_records, seeded_branch, "Desk Service")
    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [
                {
                    "id": str(part_timer.id),
                    "days_off": "",
                    "work_start_hour": 9,
                    "work_end_hour": 12,
                    "max_hours_week": 40,
                }
            ],
            "capability": {str(part_timer.id): {str(service.id): 30}},
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    day = seeded_shift["start"].astimezone(shop_timezone()).date()
    start = datetime.combine(day, time(hour=16), tzinfo=shop_timezone()).astimezone(timezone.utc)
    response = client.post(
        "/app/schedule/appointments",
        json={
            "branch_id": str(seeded_branch),
            "service_ids": [str(service.id)],
            "start_time": start.isoformat(),
            "staff_id": str(part_timer.id),
            "customer_phone": "07700900123",
            "customer_name": "Walk In",
        },
        headers=staff_headers,
    )
    assert response.status_code == 400, response.text
    assert "not working at that time" in response.json()["detail"]

    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = :i"), {"i": part_timer.id}
    )
    db_session.execute(text("DELETE FROM users WHERE phone_number = '07700900123'"))
    db_session.commit()


def test_hours_the_wrong_way_round_are_refused(client, admin_headers, part_timer):
    response = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [
                {
                    "id": str(part_timer.id),
                    "days_off": "",
                    "work_start_hour": 17,
                    "work_end_hour": 10,
                    "max_hours_week": 40,
                }
            ],
            "capability": {},
        },
        headers=admin_headers,
    )
    assert response.status_code == 422
