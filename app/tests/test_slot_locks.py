"""Slot locking: the year-round calendar is trimmed by admin/staff locks."""

from datetime import datetime, timedelta


def _overlaps(slot: dict, window_start, window_end) -> bool:
    slot_start = datetime.fromisoformat(slot["start_time"])
    slot_end = datetime.fromisoformat(slot["end_time"])
    return slot_start < window_end and slot_end > window_start


def _lock_payload(seeded_staff, seeded_shift, staff_specific=False, hours=2):
    start = seeded_shift["start"]
    payload = {
        "branch_id": str(seeded_staff["branch_id"]),
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(hours=hours)).isoformat(),
        "reason": "Fully booked with walk-ins",
    }
    if staff_specific:
        payload["staff_id"] = str(seeded_staff["staff_id"])
    return payload


def test_admin_lock_hides_slots_and_blocks_booking(
    client,
    admin_headers,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    lock = client.post(
        "/app/slot-locks",
        json=_lock_payload(seeded_staff, seeded_shift),
        headers=admin_headers,
    )
    assert lock.status_code == 201
    cleanup_records.append(("slot_locks", lock.json()["id"]))

    locked_start = seeded_shift["start"]
    locked_end = locked_start + timedelta(hours=2)

    slots = client.get(
        "/app/availability",
        params={
            "branch_id": str(seeded_staff["branch_id"]),
            "service_id": str(seeded_service),
            "date": locked_start.date().isoformat(),
        },
    ).json()
    for slot in slots:
        assert not _overlaps(slot, locked_start, locked_end)

    booking = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "staff_id": str(seeded_staff["staff_id"]),
                    "start_time": locked_start.isoformat(),
                }
            ],
        },
        headers=customer_headers,
    )
    assert booking.status_code == 409


def test_staff_without_permission_cannot_lock(client, seeded_staff, seeded_shift):
    response = client.post(
        "/app/slot-locks",
        json=_lock_payload(seeded_staff, seeded_shift),
        headers=seeded_staff["headers"],
    )
    assert response.status_code == 403


def test_admin_grants_lock_permission_then_staff_can_lock_own_branch(
    client, admin_headers, seeded_staff, seeded_shift, cleanup_records
):
    grant = client.post(
        f"/app/admin/staff/{seeded_staff['staff_id']}/slot-lock-permission",
        json={"enabled": True},
        headers=admin_headers,
    )
    assert grant.status_code == 200
    assert grant.json()["can_lock_slots"] is True

    lock = client.post(
        "/app/slot-locks",
        json=_lock_payload(seeded_staff, seeded_shift, staff_specific=True),
        headers=seeded_staff["headers"],
    )
    assert lock.status_code == 201
    cleanup_records.append(("slot_locks", lock.json()["id"]))

    listing = client.get(
        "/app/slot-locks",
        params={"branch_id": str(seeded_staff["branch_id"])},
        headers=seeded_staff["headers"],
    )
    assert listing.status_code == 200
    assert any(entry["id"] == lock.json()["id"] for entry in listing.json())

    unlock = client.delete(f"/app/slot-locks/{lock.json()['id']}", headers=seeded_staff["headers"])
    assert unlock.status_code == 204


def test_staff_with_permission_cannot_lock_other_branch(
    client, admin_headers, db_session, seeded_staff, seeded_shift, cleanup_records
):
    import uuid

    from app.branches.infrastructure.models import LocationModel

    other_branch = LocationModel(name=f"Other-{uuid.uuid4().hex[:6]}")
    db_session.add(other_branch)
    db_session.commit()
    cleanup_records.append(("locations", other_branch.id))

    client.post(
        f"/app/admin/staff/{seeded_staff['staff_id']}/slot-lock-permission",
        json={"enabled": True},
        headers=admin_headers,
    )

    payload = _lock_payload(seeded_staff, seeded_shift)
    payload["branch_id"] = str(other_branch.id)
    response = client.post("/app/slot-locks", json=payload, headers=seeded_staff["headers"])

    assert response.status_code == 403


def test_staff_specific_lock_leaves_other_staff_available(
    client,
    admin_headers,
    db_session,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    import uuid

    from app.auth.domain.value_object import UserRole, UserStatus
    from app.auth.infrastructure.models import UserModel
    from app.staff.infrastructure.models import StaffModel

    other_user = UserModel(
        phone_number=f"09{uuid.uuid4().int % 10**8:08d}",
        status=UserStatus.ACTIVE,
        role=UserRole.STAFF,
    )
    db_session.add(other_user)
    db_session.flush()
    other_staff = StaffModel(
        user_id=other_user.id, branch_id=seeded_branch, display_name="Second Staff"
    )
    db_session.add(other_staff)
    db_session.commit()
    cleanup_records.append(("users", other_user.id))
    cleanup_records.append(("staff", other_staff.id))

    lock = client.post(
        "/app/slot-locks",
        json=_lock_payload(seeded_staff, seeded_shift, staff_specific=True),
        headers=admin_headers,
    )
    assert lock.status_code == 201
    cleanup_records.append(("slot_locks", lock.json()["id"]))

    locked_start = seeded_shift["start"]
    slots = client.get(
        "/app/availability",
        params={
            "branch_id": str(seeded_branch),
            "service_id": str(seeded_service),
            "date": locked_start.date().isoformat(),
        },
    ).json()

    staff_ids_offered = {slot["staff_id"] for slot in slots}
    assert str(other_staff.id) in staff_ids_offered

    locked_end = locked_start + timedelta(hours=2)
    for slot in slots:
        if slot["staff_id"] == str(seeded_staff["staff_id"]):
            assert not _overlaps(slot, locked_start, locked_end)


def test_booking_beyond_horizon_is_rejected(
    client, customer_headers, seeded_branch, seeded_service, seeded_staff, seeded_shift
):
    far_future = seeded_shift["start"] + timedelta(days=400)
    response = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "start_time": far_future.isoformat(),
                }
            ],
        },
        headers=customer_headers,
    )
    assert response.status_code == 400


def _next_sunday(from_dt):
    days_ahead = (6 - from_dt.weekday()) % 7 or 7
    return from_dt + timedelta(days=days_ahead)


def test_sundays_have_no_availability(client, seeded_staff, seeded_service, seeded_shift):
    sunday = _next_sunday(seeded_shift["start"])

    response = client.get(
        "/app/availability",
        params={
            "branch_id": str(seeded_staff["branch_id"]),
            "service_id": str(seeded_service),
            "date": sunday.date().isoformat(),
        },
    )

    assert response.status_code == 200
    assert response.json() == []


def test_sunday_booking_is_rejected_with_facebook_hint(
    client, customer_headers, seeded_branch, seeded_service, seeded_staff, seeded_shift
):
    sunday = _next_sunday(seeded_shift["start"])

    response = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "staff_id": str(seeded_staff["staff_id"]),
                    "start_time": sunday.isoformat(),
                }
            ],
        },
        headers=customer_headers,
    )

    assert response.status_code == 400
    assert "Facebook" in response.json()["detail"]


def test_year_round_calendar_has_slots_months_ahead(
    client, seeded_staff, seeded_service, seeded_shift
):
    months_ahead = (seeded_shift["start"] + timedelta(days=180)).date()
    if months_ahead.weekday() == 6:  # Sundays are closed
        months_ahead += timedelta(days=1)
    response = client.get(
        "/app/availability",
        params={
            "branch_id": str(seeded_staff["branch_id"]),
            "service_id": str(seeded_service),
            "date": months_ahead.isoformat(),
        },
    )
    assert response.status_code == 200
    slots = response.json()
    assert slots, "expected open slots half a year ahead"
    # Slots sit on the quarter-hour grid.
    for slot in slots:
        minute = int(slot["start_time"][14:16])
        assert minute % 15 == 0
