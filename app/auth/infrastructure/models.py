import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum as SAEnum, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.domain.value_object import UserStatus
from app.shared.infrastructure.database.base import Base


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    phone_number: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[UserStatus] = mapped_column(
        SAEnum(UserStatus, name="user_status", native_enum=False, length=20),
        default=UserStatus.PENDING,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
