"""Admin user management: list/search accounts, grant and revoke staff rights."""

import uuid

from app.auth.domain.value_object import UserRole, UserStatus
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


def test_delete_user_without_history_removes_row(
    client, admin_headers, customer_identity, db_session
):
    response = client.delete(f"/app/admin/users/{customer_identity['id']}", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["mode"] == "deleted"
    assert db_session.get(UserModel, customer_identity["id"]) is None


def test_delete_user_with_bookings_anonymizes_instead(
    client,
    admin_headers,
    customer_identity,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    db_session,
    cleanup_records,
):
    booking = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(seeded_service),
                    "staff_id": str(seeded_staff["staff_id"]),
                    "start_time": seeded_shift["start"].isoformat(),
                }
            ],
        },
        headers=customer_headers,
    ).json()
    cleanup_records.append(("bookings", booking["id"]))
    cleanup_records.append(("booking_details", booking["details"][0]["id"]))

    response = client.delete(f"/app/admin/users/{customer_identity['id']}", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["mode"] == "anonymized"

    user = db_session.get(UserModel, customer_identity["id"])
    db_session.refresh(user)
    assert user is not None
    assert user.phone_number is None
    assert user.email is None
    assert user.status.value == "blocked"

    # The booking history survives for revenue reporting.
    from app.bookings.infrastructure.models import BookingModel

    assert db_session.get(BookingModel, uuid.UUID(booking["id"])) is not None


def test_admin_cannot_delete_self_or_other_admin(
    client, admin_identity, db_session, cleanup_records
):
    me = client.delete(
        f"/app/admin/users/{admin_identity['id']}", headers=admin_identity["headers"]
    )
    assert me.status_code == 400

    other_admin = UserModel(
        phone_number=f"09{uuid.uuid4().int % 10**8:08d}",
        status=UserStatus.ACTIVE,
        role=UserRole.ADMIN,
    )
    db_session.add(other_admin)
    db_session.commit()
    cleanup_records.append(("users", other_admin.id))

    response = client.delete(
        f"/app/admin/users/{other_admin.id}", headers=admin_identity["headers"]
    )
    assert response.status_code == 400
