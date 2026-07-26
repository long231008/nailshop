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
    booking_ids = {a["booking_id"] for a in body["appointments"]}
    assert confirmed["id"] in booking_ids
    assert pending["id"] not in booking_ids
    assert body["appointment_count"] == len(body["appointments"])
    assert body["expected_value"] == 20.0

    entry = next(a for a in body["appointments"] if a["booking_id"] == confirmed["id"])
    assert entry["service_name"] == "Gel Manicure"
    assert entry["staff_name"] == "Test Staff"
    assert entry["status"] == "approved"


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
    assert response.json()["appointment_count"] >= 1


def test_schedule_rejects_customers(client, customer_headers, seeded_shift):
    response = client.get(
        "/app/schedule",
        params={"date": seeded_shift["start"].date().isoformat()},
        headers=customer_headers,
    )
    assert response.status_code == 403
