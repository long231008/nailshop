import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.infrastructure.database.base import Base


class PaymentTransactionType(str, Enum):
    DEPOSIT = "deposit"
    FINAL_PAYMENT = "final_payment"
    REFUND = "refund"


class PaymentTransactionStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class PaymentTransactionModel(Base):
    __tablename__ = "payment_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bookings.id"), nullable=False
    )
    provider_transaction_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    transaction_type: Mapped[PaymentTransactionType] = mapped_column(
        SAEnum(
            PaymentTransactionType,
            name="payment_transaction_type",
            native_enum=False,
            length=20,
        ),
        nullable=False,
    )
    status: Mapped[PaymentTransactionStatus] = mapped_column(
        SAEnum(
            PaymentTransactionStatus,
            name="payment_transaction_status",
            native_enum=False,
            length=20,
        ),
        default=PaymentTransactionStatus.SUCCESS,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
