import uuid
from datetime import date as date_
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Date, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.infrastructure.database.base import Base


class AssignmentSource(str, Enum):
    # Step A of the nightly allocation placed the technician here. This is the
    # only writer today - customer wishes never claim a technician or a branch.
    AUTO = "auto"


class StaffDayAssignmentModel(Base):
    """Which salon a technician works at on one day.

    The unique constraint IS the business invariant "one tech - one day - at
    most one salon"; two salons can never both claim a technician.
    """

    __tablename__ = "staff_day_assignments"
    __table_args__ = (
        UniqueConstraint("staff_id", "day", name="uq_staff_day"),
        Index("ix_staff_day_assignments_branch_day", "branch_id", "day"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("staff.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("locations.id"), nullable=False
    )
    day: Mapped[date_] = mapped_column(Date, nullable=False)
    source: Mapped[AssignmentSource] = mapped_column(
        SAEnum(AssignmentSource, name="assignment_source", native_enum=False, length=10),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
