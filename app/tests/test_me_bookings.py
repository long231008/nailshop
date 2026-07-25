def test_me_bookings_returns_only_own_bookings(
    client,
    customer_headers,
    other_customer_headers,
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
                "start_time": seeded_shift["start"].isoformat(),
            }
        ],
    }
    booking = client.post("/app/bookings", json=payload, headers=customer_headers).json()
    cleanup_records.append(("bookings", booking["id"]))
    cleanup_records.append(("booking_details", booking["details"][0]["id"]))

    mine = client.get("/app/me/bookings", headers=customer_headers)
    others = client.get("/app/me/bookings", headers=other_customer_headers)

    assert mine.status_code == 200
    assert any(b["id"] == booking["id"] for b in mine.json())
    assert all(b["id"] != booking["id"] for b in others.json())
