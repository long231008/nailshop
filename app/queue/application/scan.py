from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.branches.infrastructure.models import LocationModel
from app.queue.infrastructure.models import QueueTicketModel, QueueTicketStatus, QueueTicketType
from app.shared.infrastructure.clock import day_bounds_utc, today_in_shop_tz


class BranchNotFoundError(Exception):
    pass


def check_in_walkin(db: Session, branch_id: UUID) -> QueueTicketModel:
    if db.get(LocationModel, branch_id) is None:
        raise BranchNotFoundError()

    today = today_in_shop_tz()
    # One writer per branch per day, so two simultaneous scans cannot pick the same
    # sequence number.
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key)::bigint)"),
        {"key": f"queue:{branch_id}:{today.isoformat()}"},
    )

    day_start, day_end = day_bounds_utc(today)
    count_today = (
        db.query(QueueTicketModel)
        .filter(
            QueueTicketModel.branch_id == branch_id,
            QueueTicketModel.ticket_type == QueueTicketType.WALKIN,
            QueueTicketModel.created_at >= day_start,
            QueueTicketModel.created_at < day_end,
        )
        .count()
    )
    # Globally unique (date + branch fragment) while the customer-facing part stays
    # a small per-branch daily number.
    ticket_number = (
        f"W-{today.strftime('%y%m%d')}-{branch_id.hex[:4].upper()}-{count_today + 1:03d}"
    )

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
