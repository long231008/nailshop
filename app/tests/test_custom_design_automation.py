import io

from fastapi import status


def test_custom_design_creation_and_list_automation(
    client, customer_headers, admin_headers, db_session, cleanup_records
):
    # Test uploading a valid custom design image
    file_data = io.BytesIO(b"fake image data content")
    files = {"file": ("design.png", file_data, "image/png")}
    data = {"description": "Automated test nail design"}

    resp = client.post("/app/custom-designs", headers=customer_headers, files=files, data=data)
    assert resp.status_code == status.HTTP_201_CREATED
    res = resp.json()
    assert "id" in res
    assert res["description"] == "Automated test nail design"
    assert res["status"] == "pending"

    design_id = res["id"]
    cleanup_records.append(("custom_designs", design_id))

    # Test listing custom designs as admin
    list_resp = client.get("/app/custom-designs", headers=admin_headers)
    assert list_resp.status_code == status.HTTP_200_OK
    designs = list_resp.json()
    assert any(d["id"] == design_id for d in designs)
