from datetime import timedelta


def _start_time(seeded_shift, offset_minutes=0):
    return (seeded_shift["start"] + timedelta(minutes=offset_minutes)).isoformat()


def test_create_booking_happy_path(
    client,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": _start_time(seeded_shift),
            }
        ],
    }

    response = client.post("/app/bookings", json=payload, headers=customer_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["total_price"] == 20.0
    assert len(body["details"]) == 1

    cleanup_records.append(("bookings", body["id"]))
    cleanup_records.append(("booking_details", body["details"][0]["id"]))


def test_create_booking_rejects_staff_conflict(
    client,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": _start_time(seeded_shift),
            }
        ],
    }
    first = client.post("/app/bookings", json=payload, headers=customer_headers)
    cleanup_records.append(("bookings", first.json()["id"]))
    cleanup_records.append(("booking_details", first.json()["details"][0]["id"]))

    overlapping_payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": _start_time(seeded_shift, offset_minutes=10),
            }
        ],
    }
    response = client.post("/app/bookings", json=overlapping_payload, headers=customer_headers)

    assert response.status_code == 409


def test_create_booking_rejects_over_daily_limit(
    client,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    # seeded_service duration is 30 min; 4 bookings x 30min = 120min (at the limit),
    # a 5th booking should push the customer's daily total over 120 minutes.
    # Slots are spaced 45 min apart to respect the 15-minute buffer between
    # appointments for the same staff member.
    for i in range(4):
        payload = {
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "start_time": _start_time(seeded_shift, offset_minutes=i * 45),
                }
            ],
        }
        response = client.post("/app/bookings", json=payload, headers=customer_headers)
        assert response.status_code == 201
        cleanup_records.append(("bookings", response.json()["id"]))
        cleanup_records.append(("booking_details", response.json()["details"][0]["id"]))

    payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "start_time": _start_time(seeded_shift, offset_minutes=4 * 45),
            }
        ],
    }
    response = client.post("/app/bookings", json=payload, headers=customer_headers)

    assert response.status_code == 409


def test_create_booking_rejects_staff_role(
    client, staff_headers, seeded_branch, seeded_service, seeded_shift
):
    payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {"service_id": str(seeded_service), "start_time": seeded_shift["start"].isoformat()}
        ],
    }

    response = client.post("/app/bookings", json=payload, headers=staff_headers)

    assert response.status_code == 403
