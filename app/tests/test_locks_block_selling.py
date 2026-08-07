"""Selling must stop counting a technician a slot lock has taken off the floor.

Locks were honoured everywhere except the one place that matters first: the
capacity ledger kept counting locked technicians as free, sold against them,
and the nightly run - which does honour locks - had nobody to seat. Found by
the full-features fuzzer on its fifth random day.
"""

from datetime import timedelta

from sqlalchemy import text

from app.services.infrastructure.models import ServiceModel
from app.slot_locks.infrastructure.models import SlotLockModel


def test_a_staff_lock_on_the_only_capable_technician_stops_the_sale(
    client,
    admin_headers,
    customer_headers,
    db_session,
    seeded_branch,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    service = ServiceModel(
        branch_id=seeded_branch,
        name="Locked Away",
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
            "capability": {str(seeded_staff["staff_id"]): {str(service.id): 30}},
        },
        headers=admin_headers,
    )
    assert save.status_code == 200, save.text

    base = seeded_shift["start"].replace(minute=0)
    lock = SlotLockModel(
        branch_id=seeded_branch,
        staff_id=seeded_staff["staff_id"],
        start_time=base,
        end_time=base + timedelta(hours=2),
        reason="Training",
    )
    db_session.add(lock)
    db_session.commit()
    cleanup_records.append(("slot_locks", lock.id))

    inside = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(service.id),
                    "start_time": (base + timedelta(minutes=30)).isoformat(),
                }
            ],
        },
        headers=customer_headers,
    )
    if inside.status_code == 201:
        cleanup_records.append(("bookings", inside.json()["id"]))
        cleanup_records.append(("booking_details", inside.json()["details"][0]["id"]))
    assert inside.status_code == 400, (
        "sold a slot the only capable technician is locked away for: " + inside.text
    )

    after = client.post(
        "/app/bookings",
        json={
            "branch_id": str(seeded_branch),
            "items": [
                {
                    "service_id": str(service.id),
                    "start_time": (base + timedelta(hours=3)).isoformat(),
                }
            ],
        },
        headers=customer_headers,
    )
    assert after.status_code == 201, "the lock must not swallow the rest of the day: " + after.text
    cleanup_records.append(("bookings", after.json()["id"]))
    cleanup_records.append(("booking_details", after.json()["details"][0]["id"]))

    db_session.execute(
        text("DELETE FROM staff_capabilities WHERE staff_id = :i"),
        {"i": seeded_staff["staff_id"]},
    )
    db_session.commit()
