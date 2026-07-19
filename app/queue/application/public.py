from uuid import UUID

from sqlalchemy.orm import Session

from app.bookings.infrastructure.models import BookingModel, BookingStatus
from app.queue.infrastructure.models import QueueTicketModel, QueueTicketStatus, QueueTicketType

ACTIVE_STATUSES = [QueueTicketStatus.WAITING, QueueTicketStatus.CALLED]


def get_admin_queue(db: Session, branch_id: UUID) -> dict:
    walkin = (
        db.query(QueueTicketModel)
        .filter(
            QueueTicketModel.branch_id == branch_id,
            QueueTicketModel.ticket_type == QueueTicketType.WALKIN,
            QueueTicketModel.status.in_(ACTIVE_STATUSES),
        )
        .order_by(QueueTicketModel.created_at)
        .all()
    )

    vip_bookings = (
        db.query(BookingModel)
        .filter(
            BookingModel.branch_id == branch_id,
            BookingModel.status == BookingStatus.APPROVED,
        )
        .order_by(BookingModel.created_at)
        .all()
    )

    return {"vip": vip_bookings, "walkin": walkin}


def get_public_queue(db: Session, branch_id: UUID) -> list[dict]:
    walkin = (
        db.query(QueueTicketModel)
        .filter(
            QueueTicketModel.branch_id == branch_id,
            QueueTicketModel.ticket_type == QueueTicketType.WALKIN,
            QueueTicketModel.status.in_(ACTIVE_STATUSES),
        )
        .order_by(QueueTicketModel.created_at)
        .all()
    )

    return [
        {"ticket_number": t.ticket_number, "status": t.status.value, "position": idx + 1}
        for idx, t in enumerate(walkin)
    ]
