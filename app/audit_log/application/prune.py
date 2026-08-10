"""Trim the audit trail without ever touching the money.

Both the dashboard's delete button and the nightly job come through here, so
there is exactly one protection rule: entries proving money moved (payments,
cancellations that carry the deposit facts) are never deleted, whatever the
cutoff. Deletion is clean - the owner chose that a trim leaves no trace of
itself either.
"""

from datetime import timedelta

from sqlalchemy.orm import Session

from app.audit_log.infrastructure.models import AuditLogModel
from app.shared.infrastructure.clock import now_utc

PROTECTED_ACTION_PREFIXES = ("payment.", "booking.cancelled")


def prune_audit_log(db: Session, older_than_days: int) -> int:
    """Delete unprotected entries older than the cutoff; returns how many.
    The caller commits."""
    cutoff = now_utc() - timedelta(days=older_than_days)
    query = db.query(AuditLogModel).filter(AuditLogModel.created_at < cutoff)
    for prefix in PROTECTED_ACTION_PREFIXES:
        query = query.filter(~AuditLogModel.action.like(prefix + "%"))
    return query.delete(synchronize_session=False)
