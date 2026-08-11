"""Trim the audit trail.

The owner's rules, by how deliberate the hand is:

- The per-row delete button may remove anything, money records included -
  one aimed click at one entry is as deliberate as it gets.
- Bulk sweeps - the dashboard's older-than button and the unattended nightly
  job - never touch entries proving money moved (payments, cancellations
  carrying the deposit facts): protect_money stays on for both.

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
