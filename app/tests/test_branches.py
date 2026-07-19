def test_create_branch_requires_admin(client):
    response = client.post(
        "/app/branches", json={"name": "Downtown", "address": "123 Main St"}
    )
    assert response.status_code == 401


def test_customer_cannot_create_branch(client, customer_headers):
    response = client.post(
        "/app/branches",
        json={"name": "Downtown", "address": "123 Main St"},
        headers=customer_headers,
    )
    assert response.status_code == 403


def test_admin_can_create_and_list_branches_with_services(
    client, admin_headers, cleanup_records
):
    branch_response = client.post(
        "/app/branches",
        json={"name": "Downtown", "address": "123 Main St", "phone_number": "0123456789"},
        headers=admin_headers,
    )
    assert branch_response.status_code == 201
    branch = branch_response.json()
    cleanup_records.append(("locations", branch["id"]))

    service_response = client.post(
        "/app/services",
        json={
            "branch_id": branch["id"],
            "name": "Gel Manicure",
            "category": "manicure",
            "duration_min": 45,
            "base_price": 25.0,
        },
        headers=admin_headers,
    )
    assert service_response.status_code == 201
    service = service_response.json()
    cleanup_records.append(("services", service["id"]))

    list_response = client.get("/app/branches")
    assert list_response.status_code == 200
    branches = {b["id"]: b for b in list_response.json()}
    assert branch["id"] in branches
    service_ids = [s["id"] for s in branches[branch["id"]]["services"]]
    assert service["id"] in service_ids
