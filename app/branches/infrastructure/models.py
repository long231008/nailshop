import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.infrastructure.database.base import Base


class LocationModel(Base):
    __tablename__ = "locations"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Fixed physical capacity of the salon: how many services of each kind can
    # run at the same instant, whatever the technician headcount. 0 means "not
    # tracked / unlimited", so existing branches keep their old behaviour until
    # a real count is entered. A service claims one of these via its `resource`
    # field (PEDI_CHAIR / TABLE / MASSAGE_BED).
    pedicure_chairs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manicure_tables: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    massage_beds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
