import uuid
from datetime import datetime, timedelta, timezone

from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.branches.infrastructure.models import LocationModel
from app.staff.infrastructure.models import StaffModel


def _create_staff(db_session, cleanup_records):
    user = UserModel(
        phone_number=f"09{uuid.uuid4().int % 10**8:08d}",
        status=UserStatus.ACTIVE,
        role=UserRole.STAFF,
    )
    branch = LocationModel(name="Test Branch", address="1 Test St")
    db_session.add_all([user, branch])
    db_session.flush()

    staff = StaffModel(user_id=user.id, branch_id=branch.id, display_name="Test Staff")
    db_session.add(staff)
    db_session.commit()

    cleanup_records.append(("users", user.id))
    cleanup_records.append(("locations", branch.id))
    cleanup_records.append(("staff", staff.id))

    return staff.id, branch.id


def _shift_payload(staff_id, branch_id, start_offset_hours=0, duration_hours=4):
    start = datetime.now(timezone.utc) + timedelta(days=1, hours=start_offset_hours)
    end = start + timedelta(hours=duration_hours)
    return {
        "staff_id": str(staff_id),
        "branch_id": str(branch_id),
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
    }


def test_create_shift(client, admin_headers, db_session, cleanup_records):
    staff_id, branch_id = _create_staff(db_session, cleanup_records)
    payload = _shift_payload(staff_id, branch_id)

    response = client.post("/app/shifts", json=payload, headers=admin_headers)

    assert response.status_code == 201
    assert response.json()["staff_id"] == str(staff_id)
    cleanup_records.append(("staff_rosters", response.json()["id"]))


def test_overlapping_shift_for_same_staff_is_rejected(
    client, admin_headers, db_session, cleanup_records
):
    staff_id, branch_id = _create_staff(db_session, cleanup_records)
    first = _shift_payload(staff_id, branch_id, start_offset_hours=0, duration_hours=4)
    first_response = client.post("/app/shifts", json=first, headers=admin_headers)
    cleanup_records.append(("staff_rosters", first_response.json()["id"]))

    overlapping = _shift_payload(staff_id, branch_id, start_offset_hours=2, duration_hours=4)
    response = client.post("/app/shifts", json=overlapping, headers=admin_headers)

    assert response.status_code == 409


def test_shift_end_before_start_is_rejected(client, admin_headers):
    start = datetime.now(timezone.utc) + timedelta(days=1)
    payload = {
        "staff_id": str(uuid.uuid4()),
        "branch_id": str(uuid.uuid4()),
        "start_time": start.isoformat(),
        "end_time": (start - timedelta(hours=1)).isoformat(),
    }

    response = client.post("/app/shifts", json=payload, headers=admin_headers)

    assert response.status_code == 422
