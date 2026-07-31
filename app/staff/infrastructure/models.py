import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.infrastructure.database.base import Base


class StaffStatus(str, Enum):
    ACTIVE = "active"
    RESERVED = "reserved"
    BLOCKED = "blocked"


class StaffModel(Base):
    __tablename__ = "staff"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("locations.id"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[StaffStatus] = mapped_column(
        SAEnum(StaffStatus, name="staff_status", native_enum=False, length=20),
        default=StaffStatus.ACTIVE,
        nullable=False,
    )
    can_price_custom_designs: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Delegated by an admin: lets this staff member lock/unlock time slots at
    # their branch (e.g. when a slot is already full with walk-ins).
    can_lock_slots: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Weekly days off as comma-separated weekday numbers (0=Monday .. 6=Sunday).
    # Feeds the capacity ledger: a day off removes the tech from that day's lanes.
    days_off: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    max_hours_week: Mapped[int] = mapped_column(Integer, nullable=False, default=40)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
