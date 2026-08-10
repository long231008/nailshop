"""Closing a salon for good - refused while anything meaningful points at it."""

import uuid

from app.branches.infrastructure.models import LocationModel
from app.staff.infrastructure.models import StaffModel


def _branch(db_session, cleanup_records):
    branch = LocationModel(name=f"Close-{uuid.uuid4().hex[:6]}", address="9 Shut St")
    db_session.add(branch)
    db_session.commit()
    cleanup_records.append(("locations", branch.id))
    return branch


def test_an_empty_branch_deletes_cleanly(client, admin_headers, db_session, cleanup_records):
    branch = _branch(db_session, cleanup_records)
    branch_id = branch.id
    db_session.expunge_all()

    response = client.delete(f"/app/branches/{branch_id}", headers=admin_headers)
    assert response.status_code == 204, response.text
    assert (
        db_session.query(LocationModel.id).filter(LocationModel.id == branch_id).first() is None
    )


def test_a_branch_with_homed_staff_is_refused_with_a_reason(
    client, admin_headers, db_session, seeded_staff, cleanup_records
):
    branch = _branch(db_session, cleanup_records)
    staff = db_session.get(StaffModel, seeded_staff["staff_id"])
    original_home = staff.branch_id
    staff.branch_id = branch.id
    db_session.commit()
    branch_id = branch.id

    response = client.delete(f"/app/branches/{branch_id}", headers=admin_headers)
    assert response.status_code == 409, response.text
    assert "Technicians" in response.json()["detail"]

    staff = db_session.get(StaffModel, seeded_staff["staff_id"])
    staff.branch_id = original_home
    db_session.commit()


def test_only_admins_may_close_a_salon(client, customer_headers, db_session, cleanup_records):
    branch = _branch(db_session, cleanup_records)
    response = client.delete(f"/app/branches/{branch.id}", headers=customer_headers)
    assert response.status_code == 403
