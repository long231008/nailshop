import io


def test_list_staff_filters_by_branch_and_status(client, admin_headers, seeded_staff):
    response = client.get(
        "/app/staff", params={"branch_id": str(seeded_staff["branch_id"])}, headers=admin_headers
    )
    assert response.status_code == 200
    ids = [s["id"] for s in response.json()]
    assert str(seeded_staff["staff_id"]) in ids

    filtered = client.get(
        "/app/staff",
        params={"branch_id": str(seeded_staff["branch_id"]), "status": "blocked"},
        headers=admin_headers,
    )
    assert filtered.status_code == 200
    assert all(s["status"] == "blocked" for s in filtered.json())


def test_list_staff_requires_admin(client, staff_headers):
    response = client.get("/app/staff", headers=staff_headers)
    assert response.status_code == 403


def test_list_shifts_filters_by_staff(client, admin_headers, seeded_staff, seeded_shift):
    response = client.get(
        "/app/shifts", params={"staff_id": str(seeded_staff["staff_id"])}, headers=admin_headers
    )
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["staff_id"] == str(seeded_staff["staff_id"])


def test_list_discounts_filters_by_type_and_active(client, admin_headers, cleanup_records):
    created = client.post(
        "/app/discounts",
        json={"name": "Gift 80", "discount_type": "gift", "value": 80},
        headers=admin_headers,
    )
    cleanup_records.append(("discounts", created.json()["id"]))

    response = client.get(
        "/app/discounts",
        params={"discount_type": "gift", "is_active": True},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert created.json()["id"] in [d["id"] for d in response.json()]


def test_list_bookings_filters(
    client, admin_headers, customer_headers, customer_identity,
    seeded_branch, seeded_service, seeded_staff, seeded_shift, cleanup_records,
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

    response = client.get(
        "/app/bookings",
        params={
            "status": "pending",
            "branch_id": str(seeded_branch),
            "customer_id": str(customer_identity["id"]),
        },
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert booking["id"] in [b["id"] for b in response.json()]

    other_status = client.get(
        "/app/bookings", params={"status": "completed"}, headers=admin_headers
    )
    assert booking["id"] not in [b["id"] for b in other_status.json()]


def test_list_custom_designs_filters_by_priced(
    client, admin_headers, customer_headers, cleanup_records
):
    created = client.post(
        "/app/custom-designs",
        files={"file": ("d.png", io.BytesIO(b"bytes"), "image/png")},
        headers=customer_headers,
    ).json()
    cleanup_records.append(("custom_designs", created["id"]))

    unpriced = client.get(
        "/app/custom-designs", params={"priced": False}, headers=admin_headers
    )
    assert created["id"] in [d["id"] for d in unpriced.json()]

    priced = client.get("/app/custom-designs", params={"priced": True}, headers=admin_headers)
    assert created["id"] not in [d["id"] for d in priced.json()]
