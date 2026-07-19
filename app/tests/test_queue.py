def test_scan_generates_sequential_ticket_numbers(client, seeded_branch, cleanup_records):
    first = client.post("/app/queue/scan", json={"branch_id": str(seeded_branch)})
    second = client.post("/app/queue/scan", json={"branch_id": str(seeded_branch)})

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["ticket_number"] == "W-001"
    assert second.json()["ticket_number"] == "W-002"

    cleanup_records.append(("queue_tickets", first.json()["id"]))
    cleanup_records.append(("queue_tickets", second.json()["id"]))


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
