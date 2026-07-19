from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.queue.infrastructure.models import QueueTicketModel, QueueTicketStatus, QueueTicketType


def check_in_walkin(db: Session, branch_id: UUID) -> QueueTicketModel:
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    count_today = (
        db.query(QueueTicketModel)
        .filter(
            QueueTicketModel.branch_id == branch_id,
            QueueTicketModel.ticket_type == QueueTicketType.WALKIN,
            QueueTicketModel.created_at >= today_start,
        )
        .count()
    )
    ticket_number = f"W-{count_today + 1:03d}"

    ticket = QueueTicketModel(
        ticket_number=ticket_number,
        branch_id=branch_id,
        ticket_type=QueueTicketType.WALKIN,
        status=QueueTicketStatus.WAITING,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket
