from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.audit_log.infrastructure.models import AuditLogModel
from app.audit_log.presentation.schemas import AuditLogEntry
from app.auth.domain.value_object import UserRole
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import require_roles

router = APIRouter(prefix="/audit-log", tags=["audit-log"])


@router.get("", response_model=list[AuditLogEntry])
def list_audit_log(
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> list[AuditLogEntry]:
    query = db.query(AuditLogModel)
    if from_ is not None:
        query = query.filter(AuditLogModel.created_at >= from_)
    if to is not None:
        query = query.filter(AuditLogModel.created_at <= to)

    entries = query.order_by(AuditLogModel.created_at.desc()).all()
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
