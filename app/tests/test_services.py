import uuid


def _create_branch(client, admin_headers, cleanup_records):
    response = client.post(
        "/app/branches",
        json={"name": f"Branch-{uuid.uuid4().hex[:6]}", "address": "1 Test St"},
        headers=admin_headers,
    )
    branch = response.json()
    cleanup_records.append(("locations", branch["id"]))
    return branch["id"]


def _create_service(client, admin_headers, cleanup_records, branch_id=None, category="manicure"):
    response = client.post(
        "/app/services",
        json={
            "branch_id": branch_id,
            "name": f"Service-{uuid.uuid4().hex[:6]}",
            "category": category,
            "duration_min": 30,
            "base_price": 20.0,
        },
        headers=admin_headers,
    )
    service = response.json()
    cleanup_records.append(("services", service["id"]))
    return service


def test_filter_services_by_branch_and_category(client, admin_headers, cleanup_records):
    branch_id = _create_branch(client, admin_headers, cleanup_records)
    matching = _create_service(client, admin_headers, cleanup_records, branch_id, "pedicure")
    _create_service(client, admin_headers, cleanup_records, None, "manicure")

    response = client.get("/app/services", params={"branch_id": branch_id, "category": "pedicure"})

    assert response.status_code == 200
    ids = [s["id"] for s in response.json()]
    assert ids == [matching["id"]]


def test_add_service_length(client, admin_headers, cleanup_records):
    service = _create_service(client, admin_headers, cleanup_records)

    response = client.post(
        f"/app/services/{service['id']}/lengths",
        json={"name": "Extra Long", "extra_price": 5.0, "extra_duration_min": 15},
        headers=admin_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["service_id"] == service["id"]
    assert body["extra_price"] == 5.0
    cleanup_records.append(("service_extensions", body["id"]))


def test_add_service_length_for_unknown_service_returns_404(client, admin_headers):
    response = client.post(
        f"/app/services/{uuid.uuid4()}/lengths",
        json={"name": "Extra Long"},
        headers=admin_headers,
    )

    assert response.status_code == 404
