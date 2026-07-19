import io


def test_create_custom_design_uploads_file(client, customer_headers, cleanup_records):
    file_content = b"fake-image-bytes"
    response = client.post(
        "/app/custom-designs",
        files={"file": ("design.png", io.BytesIO(file_content), "image/png")},
        data={"description": "Floral pattern"},
        headers=customer_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["description"] == "Floral pattern"
    assert body["image_url"].startswith("https://res.cloudinary.com/")
    assert body["estimated_price"] is None

    cleanup_records.append(("custom_designs", body["id"]))


def test_set_custom_design_price(client, customer_headers, admin_headers, cleanup_records):
    create_response = client.post(
        "/app/custom-designs",
        files={"file": ("design.png", io.BytesIO(b"bytes"), "image/png")},
        headers=customer_headers,
    )
    design_id = create_response.json()["id"]
    cleanup_records.append(("custom_designs", design_id))

    response = client.post(
        f"/app/custom-designs/{design_id}/price",
        json={"estimated_price": 15.5},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["estimated_price"] == 15.5


def test_create_custom_design_rejects_staff_role(client, staff_headers):
    response = client.post(
        "/app/custom-designs",
        files={"file": ("design.png", io.BytesIO(b"bytes"), "image/png")},
        headers=staff_headers,
    )
    assert response.status_code == 403
