"""Admin user management: list/search accounts, grant and revoke staff rights."""

import uuid

from app.auth.domain.value_object import UserRole
from app.auth.infrastructure.models import UserModel
from app.staff.infrastructure.models import StaffModel, StaffStatus


def test_admin_can_list_and_search_users(client, admin_headers, customer_identity, db_session):
    customer = db_session.get(UserModel, customer_identity["id"])

    listing = client.get("/app/admin/users", headers=admin_headers)
    assert listing.status_code == 200
    assert any(u["id"] == str(customer.id) for u in listing.json())

    search = client.get(
        "/app/admin/users",
        params={"q": customer.phone_number[-6:]},
        headers=admin_headers,
    )
    assert search.status_code == 200
    results = search.json()
    assert any(u["id"] == str(customer.id) for u in results)
    assert all(
        customer.phone_number[-6:] in (u["phone_number"] or "")
        or customer.phone_number[-6:] in (u["email"] or "")
        for u in results
    )


def test_list_users_requires_admin(client, customer_headers):
    response = client.get("/app/admin/users", headers=customer_headers)
    assert response.status_code == 403


def test_grant_staff_creates_profile_and_promotes_role(
    client, admin_headers, customer_identity, seeded_branch, db_session, cleanup_records
):
    response = client.post(
        f"/app/admin/users/{customer_identity['id']}/grant-staff",
        json={"branch_id": str(seeded_branch), "display_name": "Promoted One"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "active"
    assert body["display_name"] == "Promoted One"
    cleanup_records.append(("staff", body["staff_id"]))

    user = db_session.get(UserModel, customer_identity["id"])
    db_session.refresh(user)
    assert user.role == UserRole.STAFF

    listing = client.get("/app/admin/users", params={"role": "staff"}, headers=admin_headers).json()
    entry = next(u for u in listing if u["id"] == str(customer_identity["id"]))
    assert entry["staff_display_name"] == "Promoted One"
    assert entry["staff_branch_id"] == str(seeded_branch)


def test_revoke_staff_blocks_profile_and_demotes_role(
    client, admin_headers, customer_identity, seeded_branch, db_session, cleanup_records
):
    grant = client.post(
        f"/app/admin/users/{customer_identity['id']}/grant-staff",
        json={"branch_id": str(seeded_branch), "display_name": "Temp Staff"},
        headers=admin_headers,
    ).json()
    cleanup_records.append(("staff", grant["staff_id"]))

    revoke = client.post(
        f"/app/admin/users/{customer_identity['id']}/revoke-staff",
        headers=admin_headers,
    )

    assert revoke.status_code == 200
    assert revoke.json()["role"] == "customer"

    staff = db_session.get(StaffModel, uuid.UUID(grant["staff_id"]))
    db_session.refresh(staff)
    assert staff.status == StaffStatus.BLOCKED

    # Re-granting reactivates the same profile instead of duplicating it.
    regrant = client.post(
        f"/app/admin/users/{customer_identity['id']}/grant-staff",
        json={"branch_id": str(seeded_branch), "display_name": "Back Again"},
        headers=admin_headers,
    )
    assert regrant.status_code == 200
    assert regrant.json()["staff_id"] == grant["staff_id"]
    assert regrant.json()["status"] == "active"


def test_admin_cannot_grant_staff_to_self(client, admin_identity, seeded_branch):
    response = client.post(
        f"/app/admin/users/{admin_identity['id']}/grant-staff",
        json={"branch_id": str(seeded_branch), "display_name": "Self Promote"},
        headers=admin_identity["headers"],
    )
    assert response.status_code == 400


def test_revoke_staff_on_plain_customer_returns_404(client, admin_headers, customer_identity):
    response = client.post(
        f"/app/admin/users/{customer_identity['id']}/revoke-staff",
        headers=admin_headers,
    )
    assert response.status_code == 404
