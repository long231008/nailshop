def test_get_my_profile_returns_own_data(client, customer_identity):
    response = client.get("/app/me", headers=customer_identity["headers"])

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(customer_identity["id"])
    assert body["role"] == "customer"
    assert body["status"] == "active"
    assert body["phone_number"] is not None


def test_get_my_profile_requires_auth(client):
    response = client.get("/app/me")
    assert response.status_code == 401


def test_patch_my_profile_updates_email(client, customer_identity, unique_email):
    response = client.patch(
        "/app/me", json={"email": unique_email}, headers=customer_identity["headers"]
    )

    assert response.status_code == 200
    assert response.json()["email"] == unique_email

    refetched = client.get("/app/me", headers=customer_identity["headers"])
    assert refetched.json()["email"] == unique_email


def test_patch_my_profile_conflict_on_duplicate_email(
    client, customer_identity, other_customer_headers, unique_email
):
    taken = client.patch(
        "/app/me", json={"email": unique_email}, headers=customer_identity["headers"]
    )
    assert taken.status_code == 200

    conflict = client.patch("/app/me", json={"email": unique_email}, headers=other_customer_headers)
    assert conflict.status_code == 409
