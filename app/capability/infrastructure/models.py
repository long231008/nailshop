import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.infrastructure.database.base import Base


class StaffCapabilityModel(Base):
    """One cell of the owner-supplied capability matrix (design doc v3.2, 1.1).

    minutes = the REAL minutes this technician needs for this service. A missing
    row means the technician cannot do the service and must never be assigned it.
    The system never edits these numbers on its own - only the owner does.
    """

    __tablename__ = "staff_capabilities"
    __table_args__ = (
        UniqueConstraint("staff_id", "service_id", name="uq_capability_cell"),
        Index("ix_staff_capabilities_service", "service_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("staff.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
