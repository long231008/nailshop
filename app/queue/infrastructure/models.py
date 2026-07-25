import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.infrastructure.database.base import Base


class QueueTicketType(str, Enum):
    WALKIN = "walkin"
    BOOKING = "booking"


class QueueTicketStatus(str, Enum):
    WAITING = "waiting"
    CALLED = "called"
    IN_SERVICE = "in_service"
    DONE = "done"
    CANCELLED = "cancelled"


class QueueTicketModel(Base):
    __tablename__ = "queue_tickets"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticket_number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("locations.id"), nullable=False
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bookings.id"), nullable=True
    )
    ticket_type: Mapped[QueueTicketType] = mapped_column(
        SAEnum(QueueTicketType, name="queue_ticket_type", native_enum=False, length=20),
        nullable=False,
    )
    status: Mapped[QueueTicketStatus] = mapped_column(
        SAEnum(QueueTicketStatus, name="queue_ticket_status", native_enum=False, length=20),
        default=QueueTicketStatus.WAITING,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
