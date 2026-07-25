import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.jwt_provider import JwtTokenProvider
from app.auth.infrastructure.models import UserModel
from app.branches.infrastructure.models import LocationModel
from app.custom_designs.infrastructure.storage import get_design_storage
from app.main import app
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.cache.redis_client import get_redis
from app.shared.infrastructure.database.session import SessionLocal
from app.shared.infrastructure.rate_limit import limiter
from app.shifts.infrastructure.models import StaffRosterModel
from app.staff.infrastructure.models import StaffModel

limiter.enabled = False


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis(decode_responses=True)


class FakeDesignStorage:
    def save(self, file) -> str:
        return f"https://res.cloudinary.com/test/image/upload/fake-{uuid.uuid4().hex}.png"


@pytest.fixture
def client(fake_redis):
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_design_storage] = lambda: FakeDesignStorage()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def db_session():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def unique_phone():
    return f"09{uuid.uuid4().int % 10**8:08d}"


@pytest.fixture
def unique_email():
    return f"test-{uuid.uuid4().hex[:10]}@example.com"


def _auth_headers(user_id: uuid.UUID, role: UserRole) -> dict:
    token = JwtTokenProvider().create_access_token(user_id=user_id, role=role)
    return {"Authorization": f"Bearer {token}"}


def _create_identity(db_session, cleanup_records, role: UserRole) -> dict:
    user = UserModel(
        phone_number=f"09{uuid.uuid4().int % 10**8:08d}",
        status=UserStatus.ACTIVE,
        role=role,
    )
    db_session.add(user)
    db_session.commit()
    cleanup_records.append(("users", user.id))
    return {"id": user.id, "headers": _auth_headers(user.id, role)}


@pytest.fixture
def admin_identity(db_session, cleanup_records):
    return _create_identity(db_session, cleanup_records, UserRole.ADMIN)


@pytest.fixture
def admin_headers(admin_identity):
    return admin_identity["headers"]


@pytest.fixture
def staff_identity(db_session, cleanup_records):
    return _create_identity(db_session, cleanup_records, UserRole.STAFF)


@pytest.fixture
def staff_headers(staff_identity):
    return staff_identity["headers"]


@pytest.fixture
def customer_identity(db_session, cleanup_records):
    return _create_identity(db_session, cleanup_records, UserRole.CUSTOMER)


@pytest.fixture
def customer_headers(customer_identity):
    return customer_identity["headers"]


@pytest.fixture
def other_customer_headers(db_session, cleanup_records):
    return _create_identity(db_session, cleanup_records, UserRole.CUSTOMER)["headers"]


@pytest.fixture
def seeded_branch(db_session, cleanup_records):
    branch = LocationModel(name=f"Branch-{uuid.uuid4().hex[:6]}", address="1 Test St")
    db_session.add(branch)
    db_session.commit()
    cleanup_records.append(("locations", branch.id))
    return branch.id


@pytest.fixture
def seeded_service(db_session, cleanup_records):
    service = ServiceModel(
        branch_id=None,
        name="Gel Manicure",
        category="manicure",
        duration_min=30,
        base_price=20.0,
    )
    db_session.add(service)
    db_session.commit()
    cleanup_records.append(("services", service.id))
    return service.id


@pytest.fixture
def seeded_staff(db_session, cleanup_records, seeded_branch):
    user = UserModel(
        phone_number=f"09{uuid.uuid4().int % 10**8:08d}",
        status=UserStatus.ACTIVE,
        role=UserRole.STAFF,
    )
    db_session.add(user)
    db_session.flush()

    staff = StaffModel(user_id=user.id, branch_id=seeded_branch, display_name="Test Staff")
    db_session.add(staff)
    db_session.commit()

    cleanup_records.append(("users", user.id))
    cleanup_records.append(("staff", staff.id))

    return {
        "staff_id": staff.id,
        "user_id": user.id,
        "branch_id": seeded_branch,
        "headers": _auth_headers(user.id, UserRole.STAFF),
    }


@pytest.fixture
def seeded_shift(db_session, cleanup_records, seeded_staff):
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    start = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
    end = tomorrow.replace(hour=17, minute=0, second=0, microsecond=0)
    shift = StaffRosterModel(
        staff_id=seeded_staff["staff_id"],
        branch_id=seeded_staff["branch_id"],
        start_time=start,
        end_time=end,
    )
    db_session.add(shift)
    db_session.commit()
    cleanup_records.append(("staff_rosters", shift.id))
    return {"start": start, "end": end}


@pytest.fixture
def cleanup_identifiers(db_session):
    identifiers = {"phones": [], "emails": []}
    yield identifiers
    if identifiers["phones"] or identifiers["emails"]:
        db_session.execute(
            text("DELETE FROM users WHERE phone_number = ANY(:phones) OR email = ANY(:emails)"),
            {"phones": identifiers["phones"], "emails": identifiers["emails"]},
        )
        db_session.commit()


@pytest.fixture
def cleanup_records(db_session):
    records: list[tuple[str, object]] = []
    yield records

    all_ids = [str(record_id) for _, record_id in records]
    user_ids = [str(record_id) for table, record_id in records if table == "users"]
    location_ids = [str(record_id) for table, record_id in records if table == "locations"]
    if all_ids:
        # Audit rows are written as a side effect of the actions under test; drop
        # any that reference records we are about to delete.
        db_session.execute(
            text(
                "DELETE FROM audit_logs "
                "WHERE actor_user_id = ANY(CAST(:user_ids AS uuid[])) "
                "OR entity_id = ANY(CAST(:all_ids AS uuid[]))"
            ),
            {"user_ids": user_ids, "all_ids": all_ids},
        )
    if location_ids:
        db_session.execute(
            text("DELETE FROM queue_tickets WHERE branch_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": location_ids},
        )

    for table, record_id in reversed(records):
        db_session.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": record_id})
    db_session.commit()
