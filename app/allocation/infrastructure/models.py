import uuid
from datetime import date as date_
from datetime import datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.infrastructure.database.base import Base


class AllocationRunModel(Base):
    """One nightly (or manual) materialize run for one branch-day (doc 4.3).

    The row is the audit trail of "who decided tomorrow's assignments and when";
    re-runs (a tech calls in sick) append new rows rather than rewriting history.
    """

    __tablename__ = "allocation_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("locations.id"), nullable=False
    )
    target_date: Mapped[date_] = mapped_column(Date, nullable=False)
    assigned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unassigned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
