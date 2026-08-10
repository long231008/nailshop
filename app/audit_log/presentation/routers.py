from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.audit_log.application.prune import prune_audit_log
from app.audit_log.infrastructure.models import AuditLogModel
from app.audit_log.presentation.schemas import AuditLogEntry, AuditPruneResponse
from app.auth.domain.value_object import UserRole
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import require_roles

router = APIRouter(prefix="/audit-log", tags=["audit-log"])


@router.get("", response_model=list[AuditLogEntry])
def list_audit_log(
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    # Prefix filter: "booking." narrows to the booking lifecycle, "user." to
    # account changes, and a full action name matches exactly.
    action: str | None = Query(default=None, max_length=50),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> list[AuditLogEntry]:
    query = db.query(AuditLogModel)
    if from_ is not None:
        query = query.filter(AuditLogModel.created_at >= from_)
    if to is not None:
        query = query.filter(AuditLogModel.created_at <= to)
    if action:
        query = query.filter(AuditLogModel.action.like(action + "%"))

    entries = query.order_by(AuditLogModel.created_at.desc()).limit(limit).offset(offset).all()
    return [
        AuditLogEntry(
            id=e.id,
            actor_user_id=e.actor_user_id,
            action=e.action,
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            details=e.details,
            created_at=e.created_at,
        )
        for e in entries
    ]


@router.delete("", response_model=AuditPruneResponse)
def prune_audit_log_endpoint(
    older_than_days: int = Query(ge=30, le=3650),
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> AuditPruneResponse:
    """Delete audit entries older than the cutoff (minimum 30 days, so a typo
    cannot wipe last week). Money records - payments and cancellations that
    carry the deposit facts - are never deleted. The deletion leaves no trace
    of itself."""
    deleted = prune_audit_log(db, older_than_days)
    db.commit()
    return AuditPruneResponse(deleted=deleted)
