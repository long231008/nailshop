import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.shared.infrastructure.cache.redis_client import get_redis
from app.shared.infrastructure.database.session import SessionLocal


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def client(fake_redis):
    app.dependency_overrides[get_redis] = lambda: fake_redis
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


@pytest.fixture
def cleanup_identifiers(db_session):
    identifiers = {"phones": [], "emails": []}
    yield identifiers
    if identifiers["phones"] or identifiers["emails"]:
        db_session.execute(
            text(
                "DELETE FROM users WHERE phone_number = ANY(:phones) OR email = ANY(:emails)"
            ),
            {"phones": identifiers["phones"], "emails": identifiers["emails"]},
        )
        db_session.commit()
