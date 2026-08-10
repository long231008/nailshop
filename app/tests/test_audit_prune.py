"""Trimming the audit trail: old entries go, money records never do.

The dashboard button and the nightly job share one prune function, so this
is the single place its protection rule is proven.
"""

import uuid
from datetime import timedelta

from app.audit_log.application.prune import prune_audit_log
from app.audit_log.infrastructure.models import AuditLogModel
from app.shared.infrastructure.clock import now_utc


def _entry(db_session, cleanup_records, action, age_days):
    entry = AuditLogModel(
        actor_user_id=None,
        action=action,
        entity_type="test",
        entity_id=uuid.uuid4(),
        details={},
        created_at=now_utc() - timedelta(days=age_days),
    )
    db_session.add(entry)
    db_session.commit()
    cleanup_records.append(("audit_logs", entry.id))
    return entry


def test_prune_removes_old_entries_but_never_the_money(
    db_session, cleanup_records
):
    old_plain = _entry(db_session, cleanup_records, "booking.no_show", 800).id
    old_payment = _entry(db_session, cleanup_records, "payment.overpaid", 800).id
    old_cancel = _entry(db_session, cleanup_records, "booking.cancelled_by_customer", 800).id
    recent_plain = _entry(db_session, cleanup_records, "booking.no_show", 10).id
    db_session.expunge_all()
    markers_before = (
        db_session.query(AuditLogModel.id).filter(AuditLogModel.action == "audit.pruned").count()
    )

    deleted = prune_audit_log(db_session, older_than_days=730)
    db_session.commit()

    remaining = {row[0] for row in db_session.query(AuditLogModel.id).all()}
    assert old_plain not in remaining
    assert old_payment in remaining, "payment records are kept forever"
    assert old_cancel in remaining, "cancellations carry deposit facts - kept"
    assert recent_plain in remaining
    assert deleted >= 1

    # The owner's choice: a trim leaves no trace of itself.
    markers_after = (
        db_session.query(AuditLogModel.id).filter(AuditLogModel.action == "audit.pruned").count()
    )
    assert markers_after <= markers_before


def test_dashboard_prune_endpoint_is_admin_only_with_a_sane_minimum(
    client, admin_headers, customer_headers, db_session, cleanup_records
):
    old = _entry(db_session, cleanup_records, "slot_lock.created", 400).id
    db_session.expunge_all()

    refused = client.delete(
        "/app/audit-log", params={"older_than_days": 90}, headers=customer_headers
    )
    assert refused.status_code == 403

    typo = client.delete(
        "/app/audit-log", params={"older_than_days": 5}, headers=admin_headers
    )
    assert typo.status_code == 422, "less than 30 days must be refused"

    response = client.delete(
        "/app/audit-log", params={"older_than_days": 365}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] >= 1

    assert (
        db_session.query(AuditLogModel.id).filter(AuditLogModel.id == old).first() is None
    )
