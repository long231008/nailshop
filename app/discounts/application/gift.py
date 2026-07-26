from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.discounts.infrastructure.models import DiscountModel, DiscountType

GIFT_MESSAGE = "Congratulations! Your order qualifies for a free gift from our shop."


def find_gift_message(db: Session, branch_id: UUID, total_price: Decimal | float) -> str | None:
    """The gift a booking earns, decided by the backend's active GIFT rules.

    A GIFT discount's `value` is the booking-total threshold that triggers it.
    """
    now = datetime.now(timezone.utc)
    gift_rule = (
        db.query(DiscountModel)
        .filter(
            DiscountModel.discount_type == DiscountType.GIFT,
            DiscountModel.is_active.is_(True),
            DiscountModel.value < total_price,
            or_(DiscountModel.branch_id.is_(None), DiscountModel.branch_id == branch_id),
            or_(DiscountModel.start_at.is_(None), DiscountModel.start_at <= now),
            or_(DiscountModel.end_at.is_(None), DiscountModel.end_at >= now),
        )
        .order_by(DiscountModel.value.desc())
        .first()
    )
    return GIFT_MESSAGE if gift_rule is not None else None
