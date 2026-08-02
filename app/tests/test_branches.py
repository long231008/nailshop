import datetime as dt

from app.staff.infrastructure.models import StaffModel


def test_create_branch_requires_admin(client):
    response = client.post("/app/branches", json={"name": "Downtown", "address": "123 Main St"})
    assert response.status_code == 401


def test_customer_cannot_create_branch(client, customer_headers):
    response = client.post(
        "/app/branches",
        json={"name": "Downtown", "address": "123 Main St"},
        headers=customer_headers,
    )
    assert response.status_code == 403


def test_admin_can_create_and_list_branches_with_services(client, admin_headers, cleanup_records):
    branch_response = client.post(
        "/app/branches",
        json={"name": "Downtown", "address": "123 Main St", "phone_number": "0123456789"},
        headers=admin_headers,
    )
    assert branch_response.status_code == 201
    branch = branch_response.json()
    cleanup_records.append(("locations", branch["id"]))

    service_response = client.post(
        "/app/services",
        json={
            "branch_id": branch["id"],
            "name": "Gel Manicure",
            "category": "manicure",
            "duration_min": 45,
            "base_price": 25.0,
        },
        headers=admin_headers,
    )
    assert service_response.status_code == 201
    service = service_response.json()
    cleanup_records.append(("services", service["id"]))

    list_response = client.get("/app/branches")
    assert list_response.status_code == 200
    branches = {b["id"]: b for b in list_response.json()}
    assert branch["id"] in branches
    service_ids = [s["id"] for s in branches[branch["id"]]["services"]]
    assert service["id"] in service_ids


def test_public_technicians_list_names_only_and_respects_days_off(
    client, db_session, seeded_branch, seeded_staff
):
    """The wish picker's data source: public, names only, day-off aware."""
    response = client.get(f"/app/branches/{seeded_branch}/technicians")
    assert response.status_code == 200
    techs = response.json()
    assert {"id": str(seeded_staff["staff_id"]), "display_name": "Test Staff"} in techs
    assert all(set(t.keys()) == {"id", "display_name"} for t in techs)

    # On the tech's weekly day off the same list leaves them out.
    a_monday = dt.date(2026, 8, 10)
    staff = db_session.get(StaffModel, seeded_staff["staff_id"])
    staff.days_off = str(a_monday.weekday())
    db_session.commit()
    try:
        off_day = client.get(
            f"/app/branches/{seeded_branch}/technicians",
            params={"date": a_monday.isoformat()},
        )
        assert off_day.status_code == 200
        assert str(seeded_staff["staff_id"]) not in [t["id"] for t in off_day.json()]
    finally:
        staff.days_off = ""
        db_session.commit()


def test_technicians_list_unknown_branch_404(client):
    import uuid

    response = client.get(f"/app/branches/{uuid.uuid4()}/technicians")
    assert response.status_code == 404
