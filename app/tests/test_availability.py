from datetime import datetime, timedelta


def test_availability_returns_slots_within_shift(
    client, seeded_service, seeded_staff, seeded_shift
):
    target_date = seeded_shift["start"].date()

    response = client.get(
        "/app/availability",
        params={
            "branch_id": str(seeded_staff["branch_id"]),
            "service_id": str(seeded_service),
            "date": target_date.isoformat(),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["window"] == "open"
    slots = body["slots"]
    assert len(slots) > 0
    # Capacity mode: no technician is named until the nightly allocation.
    for slot in slots:
        assert slot["staff_id"] is None

    first_slot_start = slots[0]["start_time"]
    assert first_slot_start.startswith(target_date.isoformat())


def test_availability_excludes_booked_slot_with_buffer(
    client,
    customer_headers,
    admin_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    booking_payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": seeded_shift["start"].isoformat(),
            }
        ],
    }
    booking = client.post("/app/bookings", json=booking_payload, headers=customer_headers).json()
    cleanup_records.append(("bookings", booking["id"]))
    cleanup_records.append(("booking_details", booking["details"][0]["id"]))

    response = client.get(
        "/app/availability",
        params={
            "branch_id": str(seeded_staff["branch_id"]),
            "service_id": str(seeded_service),
            "staff_id": str(seeded_staff["staff_id"]),
            "date": seeded_shift["start"].date().isoformat(),
        },
    )

    slots = response.json()["slots"]
    booked_start = seeded_shift["start"]
    booked_end = booked_start + timedelta(minutes=30)
    for slot in slots:
        slot_start = datetime.fromisoformat(slot["start_time"])
        slot_end = datetime.fromisoformat(slot["end_time"])
        assert not (slot_start < booked_end and slot_end > booked_start)


def test_recommended_slots_are_a_hint_not_a_filter(
    client,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    """The salon's tidy times are flagged, but every sellable time stays on the
    list: the customer keeps the final say over when they come in."""
    target_date = seeded_shift["start"].date()
    params = {
        "branch_id": str(seeded_branch),
        "service_id": str(seeded_service),
        "date": target_date.isoformat(),
    }

    empty_day = client.get("/app/availability", params=params).json()["slots"]
    assert len(empty_day) > 1
    # Opening time keeps the day tight, so it is recommended; later starts are
    # still offered, just not flagged.
    assert empty_day[0]["recommended"] is True
    assert any(slot["recommended"] is False for slot in empty_day)

    booking = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "start_time": seeded_shift["start"].isoformat(),
                }
            ],
        },
        headers=customer_headers,
    ).json()
    cleanup_records.append(("bookings", booking["id"]))
    cleanup_records.append(("booking_details", booking["details"][0]["id"]))

    after = client.get("/app/availability", params=params).json()["slots"]
    booked_end = datetime.fromisoformat(booking["details"][0]["end_time"])
    # The moment that booking frees the floor is now a recommended start.
    follow_on = next(
        (s for s in after if datetime.fromisoformat(s["start_time"]) == booked_end), None
    )
    assert follow_on is not None
    assert follow_on["recommended"] is True
    # And the list still offers times that are not recommended - nothing was
    # filtered away.
    assert any(slot["recommended"] is False for slot in after)


def test_availability_unknown_service_returns_404(client, seeded_staff, seeded_shift):
    import uuid

    response = client.get(
        "/app/availability",
        params={
            "branch_id": str(seeded_staff["branch_id"]),
            "service_id": str(uuid.uuid4()),
            "date": seeded_shift["start"].date().isoformat(),
        },
    )

    assert response.status_code == 404
