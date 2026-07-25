import uuid

from app.shared.infrastructure.clock import today_in_shop_tz


def test_scan_generates_sequential_ticket_numbers(client, seeded_branch, cleanup_records):
    first = client.post("/app/queue/scan", json={"branch_id": str(seeded_branch)})
    second = client.post("/app/queue/scan", json={"branch_id": str(seeded_branch)})

    assert first.status_code == 201
    assert second.status_code == 201

    today = today_in_shop_tz()
    prefix = f"W-{today.strftime('%y%m%d')}-{seeded_branch.hex[:4].upper()}"
    assert first.json()["ticket_number"] == f"{prefix}-001"
    assert second.json()["ticket_number"] == f"{prefix}-002"

    cleanup_records.append(("queue_tickets", first.json()["id"]))
    cleanup_records.append(("queue_tickets", second.json()["id"]))


def test_scan_rejects_unknown_branch(client):
    response = client.post("/app/queue/scan", json={"branch_id": str(uuid.uuid4())})
    assert response.status_code == 404


def test_ticket_lifecycle_transitions(client, admin_headers, seeded_branch, cleanup_records):
    ticket = client.post("/app/queue/scan", json={"branch_id": str(seeded_branch)}).json()
    cleanup_records.append(("queue_tickets", ticket["id"]))

    called = client.post(f"/app/queue/{ticket['id']}/call", headers=admin_headers)
    assert called.status_code == 200
    assert called.json()["status"] == "called"

    served = client.post(f"/app/queue/{ticket['id']}/serve", headers=admin_headers)
    assert served.status_code == 200
    assert served.json()["status"] == "in_service"

    done = client.post(f"/app/queue/{ticket['id']}/done", headers=admin_headers)
    assert done.status_code == 200
    assert done.json()["status"] == "done"

    # A finished ticket cannot be cancelled.
    cancel = client.post(f"/app/queue/{ticket['id']}/cancel", headers=admin_headers)
    assert cancel.status_code == 409


def test_public_queue_hides_personal_info(client, seeded_branch, cleanup_records):
    ticket = client.post("/app/queue/scan", json={"branch_id": str(seeded_branch)}).json()
    cleanup_records.append(("queue_tickets", ticket["id"]))

    response = client.get("/app/queue/public", params={"branch_id": str(seeded_branch)})

    assert response.status_code == 200
    entries = response.json()["entries"]
    assert any(e["ticket_number"] == ticket["ticket_number"] for e in entries)
    for entry in entries:
        assert set(entry.keys()) == {"ticket_number", "status", "position"}


def test_admin_queue_requires_admin_role(client, seeded_branch):
    response = client.get("/app/queue", params={"branch_id": str(seeded_branch)})
    assert response.status_code == 401
