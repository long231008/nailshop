"""Trim the audit trail without ever touching the money.

Both the dashboard's delete button and the nightly job come through here, so
there is exactly one protection rule: entries proving money moved (payments,
cancellations that carry the deposit facts) are never deleted, whatever the
cutoff. Each prune writes its own audit entry - the trail records that the
trail was trimmed, by whom, and how far back.
"""

from datetime import timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.audit_log.infrastructure.models import AuditLogModel
from app.shared.infrastructure.clock import now_utc

PROTECTED_ACTION_PREFIXES = ("payment.", "booking.cancelled")


def prune_audit_log(db: Session, older_than_days: int, actor_user_id: UUID | None = None) -> int:
    """Delete unprotected entries older than the cutoff; returns how many.
    The caller commits."""
    cutoff = now_utc() - timedelta(days=older_than_days)
    query = db.query(AuditLogModel).filter(AuditLogModel.created_at < cutoff)
    for prefix in PROTECTED_ACTION_PREFIXES:
        query = query.filter(~AuditLogModel.action.like(prefix + "%"))
    deleted = query.delete(synchronize_session=False)

    if deleted:
        db.add(
            AuditLogModel(
                actor_user_id=actor_user_id,
                action="audit.pruned",
                entity_type="audit_log",
                details={
                    "deleted": deleted,
                    "older_than_days": older_than_days,
                    "by": "admin" if actor_user_id else "scheduled job",
                },
            )
        )
    return deleted
