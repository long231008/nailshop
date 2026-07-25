from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.queue.application.lifecycle import (
    InvalidTicketTransitionError,
    TicketNotFoundError,
    transition_ticket,
)
from app.queue.application.public import get_admin_queue, get_public_queue
from app.queue.application.scan import BranchNotFoundError, check_in_walkin
from app.queue.infrastructure.models import QueueTicketStatus
from app.queue.presentation.schemas import (
    PublicQueueEntry,
    PublicQueueResponse,
    QueueAdminResponse,
    QueueScanRequest,
    QueueTicketResponse,
    QueueVipEntry,
)
from app.shared.infrastructure.database.session import get_db
from app.shared.infrastructure.rate_limit import limiter
from app.shared.presentation.dependencies import require_roles

router = APIRouter(prefix="/queue", tags=["queue"])


def _to_ticket_response(ticket) -> QueueTicketResponse:
    return QueueTicketResponse(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        branch_id=ticket.branch_id,
        status=ticket.status.value,
        created_at=ticket.created_at,
    )


@router.post("/scan", response_model=QueueTicketResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def scan_walkin(
    request: Request,
    payload: QueueScanRequest,
    db: Session = Depends(get_db),
) -> QueueTicketResponse:
    try:
        ticket = check_in_walkin(db, payload.branch_id)
    except BranchNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")
    return _to_ticket_response(ticket)


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
        walkin=[_to_ticket_response(t) for t in data["walkin"]],
    )


@router.get("/public", response_model=PublicQueueResponse)
def get_public_queue_endpoint(
    branch_id: UUID, db: Session = Depends(get_db)
) -> PublicQueueResponse:
    entries = get_public_queue(db, branch_id)
    return PublicQueueResponse(entries=[PublicQueueEntry(**e) for e in entries])


_TICKET_ACTIONS = {
    "call": QueueTicketStatus.CALLED,
    "serve": QueueTicketStatus.IN_SERVICE,
    "done": QueueTicketStatus.DONE,
    "cancel": QueueTicketStatus.CANCELLED,
}


@router.post("/{ticket_id}/{action}", response_model=QueueTicketResponse)
def ticket_action(
    ticket_id: UUID,
    action: str,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.STAFF)),
) -> QueueTicketResponse:
    new_status = _TICKET_ACTIONS.get(action)
    if new_status is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown queue action. Use call, serve, done or cancel.",
        )

    try:
        ticket = transition_ticket(db, ticket_id, new_status)
    except TicketNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    except InvalidTicketTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    return _to_ticket_response(ticket)
