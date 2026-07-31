import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.infrastructure.database.base import Base


class ServiceModel(Base):
    __tablename__ = "services"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("locations.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # The duration printed on the menu. Customers see it; the scheduler does NOT
    # use it - scheduling always runs on each technician's real minutes from the
    # capability matrix (staff_capabilities).
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    base_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    # Capacity-ledger attributes (design doc v3.2, section 1.1).
    skill_group: Mapped[str] = mapped_column(String(30), nullable=False, default="MANI")
    body_zone: Mapped[str] = mapped_column(String(10), nullable=False, default="HANDS")
    resource: Mapped[str | None] = mapped_column(String(30), nullable=True)
    turn_weight: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False, default=1.0)
    # Cleaning/sanitising minutes after the service; counted into the lane.
    buffer_after_min: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ServiceExtensionModel(Base):
    __tablename__ = "service_extensions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("services.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    extra_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    extra_duration_min: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
