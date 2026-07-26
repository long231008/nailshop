from fastapi import status


def test_queue_walkin_scan_and_public_view(client, seeded_branch, admin_headers):
    # 1. Scan 3 walk-in tickets
    t1_resp = client.post("/app/queue/scan", json={"branch_id": str(seeded_branch)})
    assert t1_resp.status_code == status.HTTP_201_CREATED
    t1 = t1_resp.json()
    assert "ticket_number" in t1
    assert t1["status"] == "waiting"

    t2_resp = client.post("/app/queue/scan", json={"branch_id": str(seeded_branch)})
    assert t2_resp.status_code == status.HTTP_201_CREATED
    t2 = t2_resp.json()

    # 2. Check public queue endpoint
    public_resp = client.get(f"/app/queue/public?branch_id={seeded_branch}")
    assert public_resp.status_code == status.HTTP_200_OK
    entries = public_resp.json()["entries"]
    assert len(entries) >= 2
    assert entries[0]["ticket_number"] == t1["ticket_number"]
    assert entries[0]["position"] == 1
    assert entries[1]["ticket_number"] == t2["ticket_number"]
    assert entries[1]["position"] == 2

    # 3. Admin queue endpoint
    admin_resp = client.get(f"/app/queue?branch_id={seeded_branch}", headers=admin_headers)
    assert admin_resp.status_code == status.HTTP_200_OK
    admin_data = admin_resp.json()
    assert "walkin" in admin_data
    assert "vip" in admin_data
    assert any(t["id"] == t1["id"] for t in admin_data["walkin"])
