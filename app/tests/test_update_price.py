def test_update_service_price(client, admin_headers, cleanup_records):
    created = client.post(
        "/app/services",
        json={"name": "Gel Mani", "category": "manicure", "duration_min": 30, "base_price": 20.0},
        headers=admin_headers,
    ).json()
    cleanup_records.append(("services", created["id"]))

    response = client.patch(
        f"/app/services/{created['id']}", json={"base_price": 25.0}, headers=admin_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["base_price"] == 25.0
    assert body["name"] == "Gel Mani"


def test_update_service_requires_admin(client, staff_headers, cleanup_records):
    response = client.patch(
        "/app/services/00000000-0000-0000-0000-000000000000",
        json={"base_price": 25.0},
        headers=staff_headers,
    )
    assert response.status_code == 403


def test_update_service_not_found(client, admin_headers):
    response = client.patch(
        "/app/services/00000000-0000-0000-0000-000000000000",
        json={"base_price": 25.0},
        headers=admin_headers,
    )
    assert response.status_code == 404


def test_update_branch_fields(client, admin_headers, cleanup_records):
    created = client.post(
        "/app/branches", json={"name": "Old Name", "address": "1 Old St"}, headers=admin_headers
    ).json()
    cleanup_records.append(("locations", created["id"]))

    response = client.patch(
        f"/app/branches/{created['id']}",
        json={"name": "New Name", "phone_number": "0909999999"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "New Name"
    assert body["address"] == "1 Old St"
    assert body["phone_number"] == "0909999999"


def test_update_branch_not_found(client, admin_headers):
    response = client.patch(
        "/app/branches/00000000-0000-0000-0000-000000000000",
        json={"name": "X"},
        headers=admin_headers,
    )
    assert response.status_code == 404
