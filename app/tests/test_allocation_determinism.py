"""The same day must allocate the same way twice.

Legs that start together are handed technicians in the order the nightly run
reads them, while the turn ledger still has everyone level - so that read order
decides who gets whom. Ordering by start_time alone leaves it to Postgres,
which rewrites a row wherever it likes the moment anything updates it (an admin
approving a booking is enough), and the day would quietly allocate two
different ways from identical data.
"""

import uuid

import pytest
from sqlalchemy import text

from app.allocation.application.materialize import _day_details, materialize_day
from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.bookings.infrastructure.models import BookingDetailModel
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import shop_timezone
from app.staff.infrastructure.models import StaffModel


@pytest.fixture
def second_staff(db_session, cleanup_records, seeded_branch):
    user = UserModel(
        phone_number=f"09{uuid.uuid4().int % 10**8:08d}",
        status=UserStatus.ACTIVE,
        role=UserRole.STAFF,
    )
    db_session.add(user)
    db_session.flush()
    staff = StaffModel(user_id=user.id, branch_id=seeded_branch, display_name="Second Tech")
    db_session.add(staff)
    db_session.commit()
    cleanup_records.append(("users", user.id))
    cleanup_records.append(("staff", staff.id))
    return staff


def test_two_legs_at_the_same_time_allocate_the_same_way_every_run(
    client,
    admin_headers,
    customer_headers,
    other_customer_headers,
    db_session,
    seeded_branch,
    seeded_staff,
    second_staff,
    seeded_shift,
    cleanup_records,
):
    service = ServiceModel(
        branch_id=seeded_branch,
        name="Same Time",
        category="gel",
        duration_min=30,
        base_price=20.0,
        skill_group="MANI",
    )
    db_session.add(service)
    db_session.commit()
    cleanup_records.append(("services", service.id))

    save = client.put(
        "/app/capability/matrix",
        json={
            "services": [],
            "staff": [],
            "capability": {
                str(seeded_staff["staff_id"]): {str(service.id): 30},
                str(second_staff.id): {str(service.id): 30},
            },
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    start = seeded_shift["start"].isoformat()
    legs = []
    for headers in (customer_headers, other_customer_headers):
        response = client.post(
            "/app/bookings",
            json={
                "branch_id": str(seeded_branch),
                "items": [{"service_id": str(service.id), "start_time": start}],
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        cleanup_records.append(("bookings", response.json()["id"]))
        legs.append(uuid.UUID(response.json()["details"][0]["id"]))
        cleanup_records.append(("booking_details", legs[-1]))

    day = seeded_shift["start"].astimezone(shop_timezone()).date()

    def allocate():
        run = materialize_day(db_session, seeded_branch, day)
        cleanup_records.append(("allocation_runs", run.id))
        db_session.expire_all()
        return {leg: db_session.get(BookingDetailModel, leg).staff_id for leg in legs}

    first = allocate()
    assert set(first.values()) == {seeded_staff["staff_id"], second_staff.id}

    # Hand the day back and rewrite both rows in reverse. In Postgres an UPDATE
    # writes a new version of the row, usually at the end of the table, so this
    # is the physical reshuffle a day picks up from ordinary edits and approvals.
    db_session.execute(
        text("UPDATE booking_details SET staff_id = NULL WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(leg) for leg in legs]},
    )
    for leg in reversed(legs):
        db_session.execute(
            text("UPDATE booking_details SET duration_min = duration_min WHERE id = :id"),
            {"id": leg},
        )
    db_session.commit()

    # The read order the allocator depends on is settled: booked first, first.
    read = [
        detail.id for detail, _booking, _service in _day_details(db_session, seeded_branch, day)
    ]
    assert [leg for leg in read if leg in legs] == legs

    assert allocate() == first, "the same day handed the technicians out differently"

    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(seeded_staff["staff_id"]), str(second_staff.id)]},
    )
    db_session.commit()
