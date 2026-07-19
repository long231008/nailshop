def test_create_percentage_discount(client, admin_headers, cleanup_records):
    response = client.post(
        "/app/discounts",
        json={"name": "Off-peak 10%", "discount_type": "percentage", "value": 10},
        headers=admin_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["discount_type"] == "percentage"
    assert body["is_active"] is True
    cleanup_records.append(("discounts", body["id"]))


def test_percentage_discount_over_100_is_rejected(client, admin_headers):
    response = client.post(
        "/app/discounts",
        json={"name": "Too much", "discount_type": "percentage", "value": 150},
        headers=admin_headers,
    )

    assert response.status_code == 422


def test_discount_requires_admin(client, customer_headers):
    response = client.post(
        "/app/discounts",
        json={"name": "Off-peak 10%", "discount_type": "percentage", "value": 10},
        headers=customer_headers,
    )

    assert response.status_code == 403
