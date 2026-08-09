"""The dedupe script: one service list for the chain, nothing typed-in lost.

Duplicates share a name. The keeper is the booked copy if one exists, else the
most filled-in one; capability minutes and length options move onto it before
the other copies are deleted.
"""

import importlib.util
import uuid
from pathlib import Path

from app.bookings.infrastructure.models import (
    BookingDetailModel,
    BookingModel,
    BookingStatus,
)
from app.branches.infrastructure.models import LocationModel
from app.capability.infrastructure.models import StaffCapabilityModel
from app.services.infrastructure.models import ServiceExtensionModel, ServiceModel
from app.shared.infrastructure.clock import now_utc

_SPEC = importlib.util.spec_from_file_location(
    "dedupe_services", Path(__file__).resolve().parents[2] / "scripts" / "dedupe_services.py"
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
dedupe = _MODULE.dedupe


def _service(db, cleanup_records, name, branch_id=None):
    service = ServiceModel(
        branch_id=branch_id, name=name, category="gel", duration_min=30, base_price=20.0
    )
    db.add(service)
    db.commit()
    cleanup_records.append(("services", service.id))
    return service


def _tech(db, cleanup_records, branch_id, name):
    from app.auth.domain.value_object import UserRole, UserStatus
    from app.auth.infrastructure.models import UserModel
    from app.staff.infrastructure.models import StaffModel

    user = UserModel(
        phone_number=f"09{uuid.uuid4().int % 10**8:08d}",
        status=UserStatus.ACTIVE,
        role=UserRole.STAFF,
    )
    db.add(user)
    db.flush()
    staff = StaffModel(user_id=user.id, branch_id=branch_id, display_name=name)
    db.add(staff)
    db.commit()
    cleanup_records.append(("users", user.id))
    cleanup_records.append(("staff", staff.id))
    return staff


def _cell(db, staff_id, service_id, minutes):
    db.add(StaffCapabilityModel(staff_id=staff_id, service_id=service_id, minutes=minutes))
    db.commit()


def test_the_most_filled_copy_survives_and_gains_the_others_data(
    db_session, seeded_branch, cleanup_records
):
    other_branch = LocationModel(name=f"Dedupe-{uuid.uuid4().hex[:6]}", address="2 Test St")
    db_session.add(other_branch)
    db_session.commit()
    cleanup_records.append(("locations", other_branch.id))

    tech_a = _tech(db_session, cleanup_records, seeded_branch, "Dedupe A")
    tech_b = _tech(db_session, cleanup_records, seeded_branch, "Dedupe B")
    tech_c = _tech(db_session, cleanup_records, seeded_branch, "Dedupe C")

    # A unique name per run: the smoke database is shared across runs, and a
    # leftover survivor from an interrupted one must never join this group.
    group_name = f"Dedupe Gel Pedi {uuid.uuid4().hex[:6]}"
    full = _service(db_session, cleanup_records, group_name, seeded_branch)
    partial = _service(db_session, cleanup_records, group_name, other_branch.id)
    empty = _service(db_session, cleanup_records, group_name, None)
    _cell(db_session, tech_a.id, full.id, 30)
    _cell(db_session, tech_b.id, full.id, 45)
    # The partial copy knows something the full one does not: tech C, a length.
    _cell(db_session, tech_c.id, partial.id, 60)
    db_session.add(
        ServiceExtensionModel(
            service_id=partial.id, name="Long", extra_duration_min=15, extra_price=5
        )
    )
    db_session.commit()

    full_id, partial_id, empty_id = full.id, partial.id, empty.id
    tech_a_id, tech_b_id, tech_c_id = tech_a.id, tech_b.id, tech_c.id
    report = dedupe(db_session, apply=True)
    db_session.expunge_all()

    assert any(f"MERGE '{group_name}'" in line for line in report), report
    survivor = db_session.get(ServiceModel, full_id)
    assert survivor is not None
    # Copies came from several salons, so the keeper serves the whole chain.
    assert survivor.branch_id is None
    for gone in (partial_id, empty_id):
        assert (
            db_session.query(ServiceModel.id).filter(ServiceModel.id == gone).first() is None
        )
    minutes = {
        cell.staff_id: cell.minutes
        for cell in db_session.query(StaffCapabilityModel).filter_by(service_id=full_id)
    }
    assert minutes == {tech_a_id: 30, tech_b_id: 45, tech_c_id: 60}
    stretched = (
        db_session.query(ServiceExtensionModel).filter_by(service_id=full_id, name="Long").first()
    )
    assert stretched is not None
    cleanup_records.append(("service_extensions", stretched.id))


def test_a_booked_copy_is_always_the_keeper(
    db_session, seeded_branch, customer_identity, cleanup_records
):
    tech = _tech(db_session, cleanup_records, seeded_branch, "Dedupe D")
    group_name = f"Dedupe BIAB {uuid.uuid4().hex[:6]}"
    booked = _service(db_session, cleanup_records, group_name, seeded_branch)
    richer = _service(db_session, cleanup_records, group_name, seeded_branch)
    _cell(db_session, tech.id, richer.id, 40)  # more data, but no history

    booking = BookingModel(
        customer_id=customer_identity["id"],
        branch_id=seeded_branch,
        booking_date=now_utc().date(),
        status=BookingStatus.COMPLETED,
        total_price=20,
    )
    db_session.add(booking)
    db_session.flush()
    detail = BookingDetailModel(
        booking_id=booking.id,
        service_id=booked.id,
        start_time=now_utc(),
        end_time=now_utc(),
        duration_min=30,
        price=20,
    )
    db_session.add(detail)
    db_session.commit()
    cleanup_records.append(("bookings", booking.id))
    cleanup_records.append(("booking_details", detail.id))

    booked_id, richer_id = booked.id, richer.id
    tech_id = tech.id
    dedupe(db_session, apply=True)
    db_session.expunge_all()

    assert db_session.get(ServiceModel, booked_id) is not None
    assert (
        db_session.query(ServiceModel.id).filter(ServiceModel.id == richer_id).first() is None
    )
    # The richer copy's typed-in minutes moved over instead of vanishing.
    cell = (
        db_session.query(StaffCapabilityModel)
        .filter_by(service_id=booked_id, staff_id=tech_id)
        .first()
    )
    assert cell is not None and cell.minutes == 40


def test_a_dry_run_changes_nothing(db_session, seeded_branch, cleanup_records):
    group_name = f"Dedupe Dry {uuid.uuid4().hex[:6]}"
    first = _service(db_session, cleanup_records, group_name, seeded_branch)
    second = _service(db_session, cleanup_records, group_name, seeded_branch)

    first_id, second_id = first.id, second.id
    report = dedupe(db_session, apply=False)
    db_session.expunge_all()

    assert any(f"MERGE '{group_name}'" in line for line in report), report
    assert db_session.get(ServiceModel, first_id) is not None
    assert db_session.get(ServiceModel, second_id) is not None
