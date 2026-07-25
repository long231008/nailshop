from uuid import UUID

from sqlalchemy.orm import Session

from app.queue.infrastructure.models import QueueTicketModel, QueueTicketStatus


class TicketNotFoundError(Exception):
    pass


class InvalidTicketTransitionError(Exception):
    pass


_ALLOWED_TRANSITIONS: dict[QueueTicketStatus, set[QueueTicketStatus]] = {
    QueueTicketStatus.CALLED: {QueueTicketStatus.WAITING},
    QueueTicketStatus.IN_SERVICE: {QueueTicketStatus.WAITING, QueueTicketStatus.CALLED},
    QueueTicketStatus.DONE: {QueueTicketStatus.IN_SERVICE},
    QueueTicketStatus.CANCELLED: {QueueTicketStatus.WAITING, QueueTicketStatus.CALLED},
}


def transition_ticket(
    db: Session, ticket_id: UUID, new_status: QueueTicketStatus
) -> QueueTicketModel:
    ticket = db.get(QueueTicketModel, ticket_id)
    if ticket is None:
        raise TicketNotFoundError()

    allowed_from = _ALLOWED_TRANSITIONS.get(new_status, set())
    if ticket.status not in allowed_from:
        raise InvalidTicketTransitionError(
            f"Cannot move a {ticket.status.value} ticket to {new_status.value}"
        )

    ticket.status = new_status
    db.commit()
    db.refresh(ticket)
    return ticket
