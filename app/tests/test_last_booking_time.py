"""Last accepted start is 17:30; long treatments may finish after closing."""

import uuid
from datetime import datetime

from app.shared.infrastructure.clock import last_booking_utc


def _service(db_session, cleanup_records, branch_id, duration_min):
    from app.services.infrastructure.models import ServiceModel

    service = ServiceModel(
        branch_id=branch_id,
        name=f"Long-{uuid.uuid4().hex[:6]}",
        category="biab",
        duration_min=duration_min,
        base_price=40.0,
    )
    db_session.add(service)
    db_session.commit()
    cleanup_records.append(("services", service.id))
    return service


def test_availability_offers_1730_even_for_long_services(
    client, db_session, cleanup_records, seeded_branch, seeded_staff, seeded_shift
):
    service = _service(db_session, cleanup_records, seeded_branch, 90)

    response = client.get(
        "/app/availability",
        params={
            "branch_id": str(seeded_branch),
            "service_id": str(service.id),
            "date": seeded_shift["start"].date().isoformat(),
        },
    )

    assert response.status_code == 200
    slots = response.json()["slots"]
    assert slots
    last_slot_start = datetime.fromisoformat(slots[-1]["start_time"])
    assert last_slot_start == last_booking_utc(seeded_shift["start"].date())


def test_booking_at_1730_running_past_close_is_accepted(
    client,
    customer_headers,
    db_session,
    cleanup_records,
    seeded_branch,
    seeded_staff,
    seeded_shift,
):
    service = _service(db_session, cleanup_records, seeded_branch, 90)
    start = last_booking_utc(seeded_shift["start"].date())

    response = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(service.id),
                    "staff_id": str(seeded_staff["staff_id"]),
                    "start_time": start.isoformat(),
                }
            ],
        },
        headers=customer_headers,
    )

    assert response.status_code == 201
    body = response.json()
    cleanup_records.append(("bookings", body["id"]))
    cleanup_records.append(("booking_details", body["details"][0]["id"]))
    # Ends 19:00 local - after closing, by design.
    assert body["details"][0]["end_time"] > body["details"][0]["start_time"]


def test_booking_after_1730_is_rejected(
    client, customer_headers, seeded_branch, seeded_service, seeded_staff, seeded_shift
):
    from datetime import timedelta

    start = last_booking_utc(seeded_shift["start"].date()) + timedelta(minutes=15)

    response = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "staff_id": str(seeded_staff["staff_id"]),
                    "start_time": start.isoformat(),
                }
            ],
        },
        headers=customer_headers,
    )

    assert response.status_code == 400
    assert "17:30" in response.json()["detail"]
