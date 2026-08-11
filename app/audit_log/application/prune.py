"""Trim the audit trail.

Two hands trim it, with different rights - the owner's choice:

- The dashboard's buttons are a human acting deliberately, so they may
  delete anything, money records included (protect_money=False).
- The nightly job runs unattended, so it never touches entries proving money
  moved - payments, cancellations carrying the deposit facts.

Deletion is clean either way: a trim leaves no trace of itself.
"""

from datetime import timedelta

from sqlalchemy.orm import Session

from app.audit_log.infrastructure.models import AuditLogModel
from app.shared.infrastructure.clock import now_utc

PROTECTED_ACTION_PREFIXES = ("payment.", "booking.cancelled")


def prune_audit_log(db: Session, older_than_days: int, protect_money: bool = True) -> int:
    """Delete entries older than the cutoff; returns how many. The caller
    commits."""
    cutoff = now_utc() - timedelta(days=older_than_days)
    query = db.query(AuditLogModel).filter(AuditLogModel.created_at < cutoff)
    if protect_money:
        for prefix in PROTECTED_ACTION_PREFIXES:
            query = query.filter(~AuditLogModel.action.like(prefix + "%"))
    return query.delete(synchronize_session=False)
