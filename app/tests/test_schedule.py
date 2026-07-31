"""Daily schedule: confirmed appointments of a day, visible to admin and staff."""


def _confirmed_booking(
    client,
    customer_headers,
    admin_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    booking = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "staff_id": str(seeded_staff["staff_id"]),
                    "start_time": seeded_shift["start"].isoformat(),
                }
            ],
        },
        headers=customer_headers,
    ).json()
    cleanup_records.append(("bookings", booking["id"]))
    cleanup_records.append(("booking_details", booking["details"][0]["id"]))
    client.post(f"/app/bookings/{booking['id']}/approve", headers=admin_headers)
    return booking


def test_schedule_shows_confirmed_and_hides_pending(
    client,
    admin_headers,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    confirmed = _confirmed_booking(
        client,
        customer_headers,
        admin_headers,
        seeded_branch,
        seeded_service,
        seeded_staff,
        seeded_shift,
        cleanup_records,
    )
    # A second booking left pending must not appear.
    pending = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "staff_id": str(seeded_staff["staff_id"]),
                    "start_time": (seeded_shift["start"].replace(hour=13)).isoformat(),
                }
            ],
        },
        headers=customer_headers,
    ).json()
    cleanup_records.append(("bookings", pending["id"]))
    cleanup_records.append(("booking_details", pending["details"][0]["id"]))

    response = client.get(
        "/app/schedule",
        params={
            "date": seeded_shift["start"].date().isoformat(),
            "branch_id": str(seeded_branch),
        },
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()

    # The approved booking has no deposit yet, so it waits in pending; the grid
    # itself only carries deposit-secured appointments.
    grid_ids = {a["booking_id"] for a in body["appointments"]}
    assert confirmed["id"] not in grid_ids
    assert pending["id"] not in grid_ids

    stages = {p["booking_id"]: p["stage"] for p in body["pending"]}
    assert stages[confirmed["id"]] == "awaiting_deposit"
    assert stages[pending["id"]] == "awaiting_approval"


def test_deposit_paid_booking_lands_on_the_grid(
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
    import hashlib
    import hmac
    import json
    import uuid as uuid_lib

    from app.shared.infrastructure.config.settings import settings

    booking = _confirmed_booking(
        client,
        customer_headers,
        admin_headers,
        seeded_branch,
        seeded_service,
        seeded_staff,
        seeded_shift,
        cleanup_records,
    )

    body = json.dumps(
        {
            "transaction_id": str(uuid_lib.uuid4()),
            "booking_id": booking["id"],
            "amount": 6.0,
            "transaction_type": "deposit",
        }
    ).encode()
    signature = hmac.new(settings.PAYMENT_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    webhook = client.post(
        "/app/webhooks/payment",
        content=body,
        headers={"X-Signature": signature, "Content-Type": "application/json"},
    )
    assert webhook.status_code == 200

    from app.webhooks.infrastructure.models import PaymentTransactionModel

    transaction = (
        db_session.query(PaymentTransactionModel)
        .filter(PaymentTransactionModel.booking_id == booking["id"])
        .first()
    )
    cleanup_records.append(("payment_transactions", transaction.id))

    response = client.get(
        "/app/schedule",
        params={
            "date": seeded_shift["start"].date().isoformat(),
            "branch_id": str(seeded_branch),
        },
        headers=admin_headers,
    )

    body = response.json()
    entry = next(a for a in body["appointments"] if a["booking_id"] == booking["id"])
    assert entry["service_name"] == "Gel Manicure"
    assert entry["staff_name"] == "Test Staff"
    assert body["expected_value"] >= 20.0
    assert all(p["booking_id"] != booking["id"] for p in body["pending"])


def test_staff_can_approve_bookings(
    client,
    staff_headers,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    booking = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "staff_id": str(seeded_staff["staff_id"]),
                    "start_time": seeded_shift["start"].isoformat(),
                }
            ],
        },
        headers=customer_headers,
    ).json()
    cleanup_records.append(("bookings", booking["id"]))
    cleanup_records.append(("booking_details", booking["details"][0]["id"]))

    response = client.post(f"/app/bookings/{booking['id']}/approve", headers=staff_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "approved"


def test_staff_can_view_any_branch_schedule(
    client,
    admin_headers,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    staff_headers,
    cleanup_records,
):
    _confirmed_booking(
        client,
        customer_headers,
        admin_headers,
        seeded_branch,
        seeded_service,
        seeded_staff,
        seeded_shift,
        cleanup_records,
    )

    # staff_headers belongs to a user with no staff profile at this branch -
    # every staff member may see every branch.
    response = client.get(
        "/app/schedule",
        params={
            "date": seeded_shift["start"].date().isoformat(),
            "branch_id": str(seeded_branch),
        },
        headers=staff_headers,
    )

    assert response.status_code == 200
    body = response.json()
    everything = body["appointments"] + body["pending"]
    assert len(everything) >= 1
    # Revenue totals are management information - staff never receive them.
    assert body["expected_value"] is None


def test_upcoming_pending_spans_all_dates(
    client,
    admin_headers,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    from datetime import timedelta

    near = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "staff_id": str(seeded_staff["staff_id"]),
                    "start_time": seeded_shift["start"].isoformat(),
                }
            ],
        },
        headers=customer_headers,
    ).json()
    cleanup_records.append(("bookings", near["id"]))
    cleanup_records.append(("booking_details", near["details"][0]["id"]))

    # Still days out, but inside the 14-day booking horizon.
    far_start = seeded_shift["start"] + timedelta(days=7)
    if far_start.weekday() == 6:
        far_start += timedelta(days=1)
    far = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "staff_id": str(seeded_staff["staff_id"]),
                    "start_time": far_start.isoformat(),
                }
            ],
        },
        headers=customer_headers,
    ).json()
    cleanup_records.append(("bookings", far["id"]))
    cleanup_records.append(("booking_details", far["details"][0]["id"]))

    listing = client.get(
        "/app/schedule/pending",
        params={"branch_id": str(seeded_branch)},
        headers=admin_headers,
    )
    assert listing.status_code == 200
    stages = {p["booking_id"]: p["stage"] for p in listing.json()}
    assert stages[near["id"]] == "awaiting_approval"
    assert stages[far["id"]] == "awaiting_approval"

    # Granting the far one moves it to awaiting_deposit but keeps it visible.
    client.post(f"/app/bookings/{far['id']}/approve", headers=admin_headers)
    listing = client.get(
        "/app/schedule/pending",
        params={"branch_id": str(seeded_branch)},
        headers=admin_headers,
    ).json()
    stages = {p["booking_id"]: p["stage"] for p in listing}
    assert stages[far["id"]] == "awaiting_deposit"


def test_schedule_carries_the_nail_design(
    client,
    admin_headers,
    customer_headers,
    customer_identity,
    db_session,
    seeded_branch,
    seeded_addon_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    from app.custom_designs.infrastructure.models import CustomDesignModel, CustomDesignStatus

    design = CustomDesignModel(
        customer_id=customer_identity["id"],
        image_url="https://example.com/art.jpg",
        description="Chrome french tips",
        estimated_price=22.0,
        status=CustomDesignStatus.PRICED,
    )
    db_session.add(design)
    db_session.commit()
    cleanup_records.append(("custom_designs", design.id))

    booking = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_addon_service),
                    "staff_id": str(seeded_staff["staff_id"]),
                    "start_time": seeded_shift["start"].isoformat(),
                    "custom_design_id": str(design.id),
                }
            ],
        },
        headers=customer_headers,
    ).json()
    cleanup_records.append(("bookings", booking["id"]))
    cleanup_records.append(("booking_details", booking["details"][0]["id"]))

    pending = client.get(
        "/app/schedule/pending",
        params={"branch_id": str(seeded_branch)},
        headers=admin_headers,
    ).json()
    entry = next(p for p in pending if p["booking_id"] == booking["id"])
    assert entry["design_image_url"] == "https://example.com/art.jpg"
    assert entry["design_description"] == "Chrome french tips"


def test_schedule_rejects_customers(client, customer_headers, seeded_shift):
    response = client.get(
        "/app/schedule",
        params={"date": seeded_shift["start"].date().isoformat()},
        headers=customer_headers,
    )
    assert response.status_code == 403
