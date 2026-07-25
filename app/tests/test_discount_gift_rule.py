def test_booking_over_gift_threshold_returns_gift_message(
    client,
    admin_headers,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    discount_response = client.post(
        "/app/discounts",
        json={"name": "Big spender gift", "discount_type": "gift", "value": 15},
        headers=admin_headers,
    )
    assert discount_response.status_code == 201
    cleanup_records.append(("discounts", discount_response.json()["id"]))

    # seeded_service costs 20.0, which is above the 15 threshold
    payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": seeded_shift["start"].isoformat(),
            }
        ],
    }
    response = client.post("/app/bookings", json=payload, headers=customer_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["gift_message"] == (
        "Congratulations! Your order qualifies for a free gift from our shop."
    )

    cleanup_records.append(("bookings", body["id"]))
    cleanup_records.append(("booking_details", body["details"][0]["id"]))


def test_booking_under_gift_threshold_has_no_gift_message(
    client,
    admin_headers,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    discount_response = client.post(
        "/app/discounts",
        json={"name": "High spender gift", "discount_type": "gift", "value": 80},
        headers=admin_headers,
    )
    cleanup_records.append(("discounts", discount_response.json()["id"]))

    # seeded_service costs 20.0, which is below the 80 threshold
    payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": seeded_shift["start"].isoformat(),
            }
        ],
    }
    response = client.post("/app/bookings", json=payload, headers=customer_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["gift_message"] is None

    cleanup_records.append(("bookings", body["id"]))
    cleanup_records.append(("booking_details", body["details"][0]["id"]))


def test_gift_rule_scoped_to_other_branch_does_not_apply(
    client,
    admin_headers,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    import uuid

    other_branch_response = client.post(
        "/app/branches",
        json={"name": f"Other-{uuid.uuid4().hex[:6]}", "address": "2 Test St"},
        headers=admin_headers,
    )
    cleanup_records.append(("locations", other_branch_response.json()["id"]))

    discount_response = client.post(
        "/app/discounts",
        json={
            "name": "Other branch gift",
            "discount_type": "gift",
            "value": 15,
            "branch_id": other_branch_response.json()["id"],
        },
        headers=admin_headers,
    )
    cleanup_records.append(("discounts", discount_response.json()["id"]))

    payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": seeded_shift["start"].isoformat(),
            }
        ],
    }
    response = client.post("/app/bookings", json=payload, headers=customer_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["gift_message"] is None

    cleanup_records.append(("bookings", body["id"]))
    cleanup_records.append(("booking_details", body["details"][0]["id"]))
