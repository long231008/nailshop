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


def test_patch_my_profile_updates_names(client, customer_identity):
    response = client.patch(
        "/app/me",
        json={"first_name": "Mai", "surname": "Tran"},
        headers=customer_identity["headers"],
    )

    assert response.status_code == 200
    assert response.json()["first_name"] == "Mai"

    refetched = client.get("/app/me", headers=customer_identity["headers"])
    assert refetched.json()["surname"] == "Tran"


def test_patch_my_profile_ignores_email(client, customer_identity, unique_email):
    """Email moved to the verified change flow; sending it here does nothing."""
    response = client.patch(
        "/app/me", json={"email": unique_email}, headers=customer_identity["headers"]
    )

    assert response.status_code == 200
    assert response.json()["email"] is None
