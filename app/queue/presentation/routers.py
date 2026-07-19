from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.queue.application.public import get_admin_queue, get_public_queue
from app.queue.application.scan import check_in_walkin
from app.queue.presentation.schemas import (
    PublicQueueEntry,
    PublicQueueResponse,
    QueueAdminResponse,
    QueueScanRequest,
    QueueTicketResponse,
    QueueVipEntry,
)
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import require_roles

router = APIRouter(prefix="/queue", tags=["queue"])


@router.post("/scan", response_model=QueueTicketResponse, status_code=status.HTTP_201_CREATED)
def scan_walkin(
    payload: QueueScanRequest,
    db: Session = Depends(get_db),
) -> QueueTicketResponse:
    ticket = check_in_walkin(db, payload.branch_id)
    return QueueTicketResponse(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        branch_id=ticket.branch_id,
        status=ticket.status.value,
        created_at=ticket.created_at,
    )


@router.get("", response_model=QueueAdminResponse)
def get_queue(
    branch_id: UUID,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> QueueAdminResponse:
    data = get_admin_queue(db, branch_id)
    return QueueAdminResponse(
        vip=[
            QueueVipEntry(booking_id=b.id, booking_date=b.booking_date, status=b.status.value)
            for b in data["vip"]
        ],
        walkin=[
            QueueTicketResponse(
                id=t.id,
                ticket_number=t.ticket_number,
                branch_id=t.branch_id,
                status=t.status.value,
                created_at=t.created_at,
            )
            for t in data["walkin"]
        ],
    )


@router.get("/public", response_model=PublicQueueResponse)
def get_public_queue_endpoint(
    branch_id: UUID, db: Session = Depends(get_db)
) -> PublicQueueResponse:
    entries = get_public_queue(db, branch_id)
    return PublicQueueResponse(entries=[PublicQueueEntry(**e) for e in entries])
