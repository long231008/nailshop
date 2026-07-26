"""Cart-style bookings: several services in one request, gift applied by total."""

import uuid
from datetime import datetime, timedelta


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _second_service(db_session, cleanup_records, branch_id):
    from app.services.infrastructure.models import ServiceModel

    service = ServiceModel(
        branch_id=branch_id,
        name=f"Pedicure-{uuid.uuid4().hex[:6]}",
        category="polish",
        duration_min=45,
        base_price=35.0,
    )
    db_session.add(service)
    db_session.commit()
    cleanup_records.append(("services", service.id))
    return service


def test_cart_books_services_back_to_back_with_same_staff(
    client,
    customer_headers,
    db_session,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    second = _second_service(db_session, cleanup_records, seeded_branch)
    first_start = seeded_shift["start"]
    second_start = first_start + timedelta(minutes=30)

    payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": _iso(first_start),
            },
            {
                "service_id": str(second.id),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": _iso(second_start),
            },
        ],
    }
    response = client.post("/app/bookings", json=payload, headers=customer_headers)

    assert response.status_code == 201
    body = response.json()
    assert len(body["details"]) == 2
    assert body["total_price"] == 55.0

    cleanup_records.append(("bookings", body["id"]))
    for detail in body["details"]:
        cleanup_records.append(("booking_details", detail["id"]))


def test_cart_rejects_same_staff_at_same_time(
    client,
    customer_headers,
    db_session,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    second = _second_service(db_session, cleanup_records, seeded_branch)
    start = seeded_shift["start"]

    payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": _iso(start),
            },
            {
                "service_id": str(second.id),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": _iso(start),
            },
        ],
    }
    response = client.post("/app/bookings", json=payload, headers=customer_headers)

    assert response.status_code == 409


def test_cart_total_earns_gift_from_backend_rule(
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
    discount = client.post(
        "/app/discounts",
        json={"name": f"Cart gift {uuid.uuid4().hex[:6]}", "discount_type": "gift", "value": 50},
        headers=admin_headers,
    ).json()
    cleanup_records.append(("discounts", discount["id"]))

    second = _second_service(db_session, cleanup_records, seeded_branch)
    first_start = seeded_shift["start"]

    # 20.0 alone stays under the 50 threshold, 20.0 + 35.0 goes over it.
    payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": _iso(first_start),
            },
            {
                "service_id": str(second.id),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": _iso(first_start + timedelta(minutes=30)),
            },
        ],
    }
    response = client.post("/app/bookings", json=payload, headers=customer_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["gift_message"] is not None

    cleanup_records.append(("bookings", body["id"]))
    for detail in body["details"]:
        cleanup_records.append(("booking_details", detail["id"]))


def test_gift_preview_endpoint_is_public_and_matches_rules(
    client, admin_headers, seeded_branch, cleanup_records
):
    discount = client.post(
        "/app/discounts",
        json={
            "name": f"Preview gift {uuid.uuid4().hex[:6]}",
            "discount_type": "gift",
            "value": 40,
            "branch_id": str(seeded_branch),
        },
        headers=admin_headers,
    ).json()
    cleanup_records.append(("discounts", discount["id"]))

    over = client.get(
        "/app/discounts/gift-preview",
        params={"branch_id": str(seeded_branch), "total": 45},
    )
    assert over.status_code == 200
    assert over.json()["gift_message"] is not None

    under = client.get(
        "/app/discounts/gift-preview",
        params={"branch_id": str(seeded_branch), "total": 30},
    )
    assert under.status_code == 200
    assert under.json()["gift_message"] is None


def test_availability_accepts_cart_duration_override(
    client, seeded_staff, seeded_service, seeded_shift
):
    params = {
        "branch_id": str(seeded_staff["branch_id"]),
        "service_id": str(seeded_service),
        "date": seeded_shift["start"].date().isoformat(),
        "duration_min": 75,
    }
    response = client.get("/app/availability", params=params)

    assert response.status_code == 200
    slots = response.json()
    assert slots
    for slot in slots:
        start = datetime.fromisoformat(slot["start_time"])
        end = datetime.fromisoformat(slot["end_time"])
        assert (end - start) == timedelta(minutes=75)
