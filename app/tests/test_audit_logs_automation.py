import uuid
from datetime import datetime, timedelta, timezone

from fastapi import status

from app.audit_log.infrastructure.models import AuditLogModel


def test_audit_log_access_control(client, customer_headers, admin_headers):
    # 1. No token -> 401
    resp_unauth = client.get("/app/audit-log")
    assert resp_unauth.status_code == status.HTTP_401_UNAUTHORIZED

    # 2. Customer -> 403 Forbidden
    resp_cust = client.get("/app/audit-log", headers=customer_headers)
    assert resp_cust.status_code == status.HTTP_403_FORBIDDEN

    # 3. Admin -> 200 OK
    resp_admin = client.get("/app/audit-log", headers=admin_headers)
    assert resp_admin.status_code == status.HTTP_200_OK
    assert isinstance(resp_admin.json(), list)


def test_audit_log_filtering_by_date(client, admin_headers, db_session, cleanup_records):
    now = datetime.now(timezone.utc)

    # Insert test audit log entry
    log_entry = AuditLogModel(
        actor_user_id=None,
        action="test.automated_action",
        entity_type="test_entity",
        entity_id=uuid.uuid4(),
        details={"test": "automation_data"},
        created_at=now,
    )
    db_session.add(log_entry)
    db_session.commit()
    cleanup_records.append(("audit_logs", log_entry.id))

    # Query with date range including `now`
    from_time = (now - timedelta(minutes=5)).isoformat()
    to_time = (now + timedelta(minutes=5)).isoformat()

    resp = client.get(
        "/app/audit-log", params={"from": from_time, "to": to_time}, headers=admin_headers
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert any(entry["id"] == str(log_entry.id) for entry in data)

    # Query with future date range excluding `now`
    future_from = (now + timedelta(days=1)).isoformat()
    future_to = (now + timedelta(days=2)).isoformat()
    resp_future = client.get(
        "/app/audit-log", params={"from": future_from, "to": future_to}, headers=admin_headers
    )
    assert resp_future.status_code == status.HTTP_200_OK
    data_future = resp_future.json()
    assert not any(entry["id"] == str(log_entry.id) for entry in data_future)
