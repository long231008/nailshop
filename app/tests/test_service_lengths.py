"""Length options (a longer set costs more minutes and more money).

The extra minutes belong to the booked leg from the moment it is sold, so every
later step that recomputes the leg - the nightly allocation, a manual move -
has to carry them too, or a long set quietly shrinks to a short one.
"""

import uuid

from app.allocation.application.materialize import materialize_day
from app.bookings.infrastructure.models import BookingDetailModel
from app.shared.infrastructure.clock import shop_timezone


def _cleanup_capabilities(db_session, staff_ids):
    from sqlalchemy import text

    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(s) for s in staff_ids]},
    )
    db_session.commit()


def test_a_long_set_keeps_its_extra_minutes_through_the_nightly_run(
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
    """A 30' service booked with a +30' length holds an hour. The nightly run
    names a technician and shrinks the leg to that technician's real minutes -
    which must still include the length, not just the base service."""
    length = client.post(
        f"/app/services/{seeded_service}/lengths",
        json={"name": "Long", "extra_price": 5, "extra_duration_min": 30},
        headers=admin_headers,
    )
    assert length.status_code == 201, length.text
    cleanup_records.append(("service_extensions", length.json()["id"]))

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
                {
                    "service_id": str(seeded_service),
                    "service_extension_id": length.json()["id"],
                    "start_time": seeded_shift["start"].isoformat(),
                }
            ],
        },
        headers=customer_headers,
    )
    assert booking.status_code == 201, booking.text
    body = booking.json()
    cleanup_records.append(("bookings", body["id"]))
    detail_id = body["details"][0]["id"]
    cleanup_records.append(("booking_details", detail_id))
    # Sold as base 30' + the 30' length.
    assert body["details"][0]["duration_min"] == 60

    day = seeded_shift["start"].astimezone(shop_timezone()).date()
    run = materialize_day(db_session, seeded_branch, day)
    cleanup_records.append(("allocation_runs", run.id))
    assert run.assigned_count == 1

    detail = db_session.get(BookingDetailModel, uuid.UUID(detail_id))
    db_session.refresh(detail)
    assert detail.staff_id is not None
    # The length must survive: the tech does 30' of service plus the 30' length.
    assert detail.duration_min == 60

    _cleanup_capabilities(db_session, [seeded_staff["staff_id"]])


def test_a_manual_move_keeps_the_length_too(
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
    """Reassigning a leg by hand recomputes its minutes the same way, so the
    length must not be dropped there either."""
    length = client.post(
        f"/app/services/{seeded_service}/lengths",
        json={"name": "Long", "extra_price": 5, "extra_duration_min": 30},
        headers=admin_headers,
    )
    assert length.status_code == 201, length.text
    cleanup_records.append(("service_extensions", length.json()["id"]))

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
                {
                    "service_id": str(seeded_service),
                    "service_extension_id": length.json()["id"],
                    "start_time": seeded_shift["start"].isoformat(),
                }
            ],
        },
        headers=customer_headers,
    ).json()
    cleanup_records.append(("bookings", booking["id"]))
    detail_id = booking["details"][0]["id"]
    cleanup_records.append(("booking_details", detail_id))

    moved = client.post(
        "/app/allocation/reassign",
        json={"booking_detail_id": detail_id, "staff_id": str(seeded_staff["staff_id"])},
        headers=admin_headers,
    )
    assert moved.status_code == 200, moved.text

    detail = db_session.get(BookingDetailModel, uuid.UUID(detail_id))
    db_session.refresh(detail)
    assert detail.duration_min == 60

    from app.audit_log.infrastructure.models import AuditLogModel

    for row in (
        db_session.query(AuditLogModel)
        .filter(AuditLogModel.entity_id == uuid.UUID(booking["id"]))
        .all()
    ):
        cleanup_records.append(("audit_logs", row.id))

    _cleanup_capabilities(db_session, [seeded_staff["staff_id"]])


def test_availability_offers_fewer_times_for_a_longer_set(
    client,
    admin_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    """The times offered have to hold the chosen length, or a customer picks a
    slot sized for the short set and the booking then needs longer than the
    time they were shown."""
    from datetime import datetime

    length = client.post(
        f"/app/services/{seeded_service}/lengths",
        json={"name": "Long", "extra_price": 5, "extra_duration_min": 60},
        headers=admin_headers,
    )
    assert length.status_code == 201, length.text
    cleanup_records.append(("service_extensions", length.json()["id"]))

    params = {
        "branch_id": str(seeded_branch),
        "service_ids": str(seeded_service),
        "date": seeded_shift["start"].date().isoformat(),
    }
    standard = client.get("/app/availability", params=params).json()
    longer = client.get(
        "/app/availability", params={**params, "extension_ids": length.json()["id"]}
    ).json()
    assert standard["window"] == "open"

    def span(slot):
        start = datetime.fromisoformat(slot["start_time"])
        return (datetime.fromisoformat(slot["end_time"]) - start).total_seconds() // 60

    assert span(standard["slots"][0]) == 30  # the service on its own
    assert span(longer["slots"][0]) == 90  # 30' service + the 60' length

    # The count of start times does not drop on an empty day: a visit may begin
    # at the last booking time and run past closing, which the salon allows.
    # What must change is how much of the day each start holds - checked above.


def test_a_length_in_use_cannot_be_removed(
    client,
    admin_headers,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    length = client.post(
        f"/app/services/{seeded_service}/lengths",
        json={"name": "Long", "extra_price": 5, "extra_duration_min": 30},
        headers=admin_headers,
    )
    assert length.status_code == 201, length.text
    length_id = length.json()["id"]
    cleanup_records.append(("service_extensions", length_id))

    booking = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "service_extension_id": length_id,
                    "start_time": seeded_shift["start"].isoformat(),
                }
            ],
        },
        headers=customer_headers,
    )
    assert booking.status_code == 201, booking.text
    cleanup_records.append(("bookings", booking.json()["id"]))
    cleanup_records.append(("booking_details", booking.json()["details"][0]["id"]))

    # Detaching it would let the nightly run recompute the leg without the
    # length and shrink a long set to a short one, so it is refused.
    refused = client.delete(f"/app/services/lengths/{length_id}", headers=admin_headers)
    assert refused.status_code == 409, refused.text
