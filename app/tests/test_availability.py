from datetime import timedelta


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
    slots = response.json()
    assert len(slots) > 0
    for slot in slots:
        assert slot["staff_id"] == str(seeded_staff["staff_id"])

    first_slot_start = slots[0]["start_time"]
    assert first_slot_start.startswith(target_date.isoformat())


def test_availability_excludes_booked_slot_with_buffer(
    client, customer_headers, admin_headers, seeded_branch, seeded_service, seeded_staff,
    seeded_shift, cleanup_records,
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

    slots = response.json()
    booked_start = seeded_shift["start"]
    booked_end = booked_start + timedelta(minutes=30)
    for slot in slots:
        slot_start = slot["start_time"]
        slot_end = slot["end_time"]
        assert not (slot_start < booked_end.isoformat() and slot_end > booked_start.isoformat())


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
