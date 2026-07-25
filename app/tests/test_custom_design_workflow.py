import io


def _upload_design(client, customer_headers):
    return client.post(
        "/app/custom-designs",
        files={"file": ("design.png", io.BytesIO(b"bytes"), "image/png")},
        headers=customer_headers,
    ).json()


def test_staff_without_permission_cannot_price_design(
    client, customer_headers, seeded_staff, cleanup_records
):
    design = _upload_design(client, customer_headers)
    cleanup_records.append(("custom_designs", design["id"]))

    response = client.post(
        f"/app/custom-designs/{design['id']}/price",
        json={"estimated_price": 30.0},
        headers=seeded_staff["headers"],
    )

    assert response.status_code == 403


def test_admin_grants_permission_then_staff_can_price(
    client, admin_headers, customer_headers, seeded_staff, cleanup_records
):
    grant = client.post(
        f"/app/admin/staff/{seeded_staff['staff_id']}/design-pricing",
        json={"enabled": True},
        headers=admin_headers,
    )
    assert grant.status_code == 200
    assert grant.json()["can_price_custom_designs"] is True

    design = _upload_design(client, customer_headers)
    cleanup_records.append(("custom_designs", design["id"]))

    response = client.post(
        f"/app/custom-designs/{design['id']}/price",
        json={"estimated_price": 30.0},
        headers=seeded_staff["headers"],
    )

    assert response.status_code == 200
    assert response.json()["status"] == "priced"
    assert response.json()["estimated_price"] == 30.0


def test_full_accept_flow_creates_booking_with_design_price(
    client,
    admin_headers,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
    db_session,
):
    design = _upload_design(client, customer_headers)
    cleanup_records.append(("custom_designs", design["id"]))

    client.post(
        f"/app/custom-designs/{design['id']}/price",
        json={"estimated_price": 99.0},
        headers=admin_headers,
    )

    response = client.post(
        f"/app/custom-designs/{design['id']}/accept",
        json={
            "branch_id": str(seeded_branch),
            "service_id": str(seeded_service),
            "staff_id": str(seeded_staff["staff_id"]),
            "start_time": seeded_shift["start"].isoformat(),
        },
        headers=customer_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["price"] == 99.0
    assert body["booking_status"] == "pending"
    cleanup_records.append(("bookings", body["booking_id"]))

    booking_status = client.get(
        f"/app/bookings/{body['booking_id']}/status", headers=customer_headers
    )
    assert booking_status.json()["total_price"] == 99.0

    my_bookings = client.get("/app/me/bookings", headers=customer_headers).json()
    assert any(b["id"] == body["booking_id"] for b in my_bookings)

    from app.bookings.infrastructure.models import BookingDetailModel

    detail = (
        db_session.query(BookingDetailModel)
        .filter(BookingDetailModel.booking_id == body["booking_id"])
        .first()
    )
    assert str(detail.custom_design_id) == design["id"]
    assert float(detail.price) == 99.0
    cleanup_records.append(("booking_details", detail.id))


def test_cannot_accept_unpriced_design(
    client,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    design = _upload_design(client, customer_headers)
    cleanup_records.append(("custom_designs", design["id"]))

    response = client.post(
        f"/app/custom-designs/{design['id']}/accept",
        json={
            "branch_id": str(seeded_branch),
            "service_id": str(seeded_service),
            "staff_id": str(seeded_staff["staff_id"]),
            "start_time": seeded_shift["start"].isoformat(),
        },
        headers=customer_headers,
    )

    assert response.status_code == 400


def test_reject_design(client, admin_headers, customer_headers, cleanup_records):
    design = _upload_design(client, customer_headers)
    cleanup_records.append(("custom_designs", design["id"]))

    client.post(
        f"/app/custom-designs/{design['id']}/price",
        json={"estimated_price": 40.0},
        headers=admin_headers,
    )

    response = client.post(f"/app/custom-designs/{design['id']}/reject", headers=customer_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"

    again = client.post(f"/app/custom-designs/{design['id']}/reject", headers=customer_headers)
    assert again.status_code == 400


def test_other_customer_cannot_decide_on_design(
    client,
    customer_headers,
    other_customer_headers,
    cleanup_records,
):
    design = _upload_design(client, customer_headers)
    cleanup_records.append(("custom_designs", design["id"]))

    response = client.post(
        f"/app/custom-designs/{design['id']}/reject", headers=other_customer_headers
    )

    assert response.status_code == 403


def test_me_custom_designs_shows_own_designs_only(
    client,
    customer_headers,
    other_customer_headers,
    cleanup_records,
):
    mine = _upload_design(client, customer_headers)
    cleanup_records.append(("custom_designs", mine["id"]))
    theirs = _upload_design(client, other_customer_headers)
    cleanup_records.append(("custom_designs", theirs["id"]))

    response = client.get("/app/me/custom-designs", headers=customer_headers)

    assert response.status_code == 200
    ids = [d["id"] for d in response.json()]
    assert mine["id"] in ids
    assert theirs["id"] not in ids
