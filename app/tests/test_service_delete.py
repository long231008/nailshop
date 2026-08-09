"""Removing a service row - the cure for accidentally duplicated menu items.

Deletion is refused once any booking references the service, because past
visits read their price and duration from that row. An unused duplicate goes
away cleanly, taking its capability cells and length options along.
"""

import uuid

from sqlalchemy import text

from app.capability.infrastructure.models import StaffCapabilityModel
from app.services.infrastructure.models import ServiceExtensionModel, ServiceModel


def _service(db_session, cleanup_records, name):
    service = ServiceModel(
        branch_id=None,
        name=name,
        category="gel",
        duration_min=30,
        base_price=20.0,
    )
    db_session.add(service)
    db_session.commit()
    cleanup_records.append(("services", service.id))
    return service


def test_an_unused_duplicate_disappears_with_its_cells_and_lengths(
    client, admin_headers, db_session, seeded_staff, cleanup_records
):
    duplicate = _service(db_session, cleanup_records, "Doubled Gel")
    extension = ServiceExtensionModel(
        service_id=duplicate.id, name="Long", extra_duration_min=15, extra_price=5
    )
    db_session.add(extension)
    db_session.commit()
    db_session.add(
        StaffCapabilityModel(
            staff_id=seeded_staff["staff_id"], service_id=duplicate.id, minutes=30
        )
    )
    db_session.commit()
    # Plain values, and instances out of the session: the API deletes the rows
    # in its own session, so touching the stale instances afterwards would
    # raise instead of answering.
    duplicate_id, extension_id = duplicate.id, extension.id
    db_session.expunge_all()

    response = client.delete(f"/app/services/{duplicate_id}", headers=admin_headers)
    assert response.status_code == 204, response.text

    assert (
        db_session.query(ServiceModel.id).filter(ServiceModel.id == duplicate_id).first() is None
    )
    assert (
        db_session.query(ServiceExtensionModel.id)
        .filter(ServiceExtensionModel.id == extension_id)
        .first()
        is None
    )
    cells = db_session.execute(
        text("SELECT count(*) FROM staff_capabilities WHERE service_id = :s"),
        {"s": duplicate_id},
    ).scalar()
    assert cells == 0


def test_a_booked_service_cannot_be_removed(
    client,
    admin_headers,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    booked = client.post(
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
    )
    assert booked.status_code == 201, booked.text
    cleanup_records.append(("bookings", booked.json()["id"]))
    cleanup_records.append(("booking_details", booked.json()["details"][0]["id"]))

    response = client.delete(f"/app/services/{seeded_service}", headers=admin_headers)
    assert response.status_code == 409, response.text


def test_deleting_a_missing_service_is_a_404(client, admin_headers):
    response = client.delete(f"/app/services/{uuid.uuid4()}", headers=admin_headers)
    assert response.status_code == 404, response.text
