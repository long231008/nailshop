import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.infrastructure.models import UserModel
from app.notification.application import messages
from app.notification.domain.notification import Notification, mask_destination
from app.notification.domain.sender import NotificationSender

logger = logging.getLogger(__name__)


def _send(sender: NotificationSender, notification: Notification) -> None:
    """Delivery must never break the business action that triggered it."""
    if notification.destination is None:
        logger.warning(
            "Skipping %r notification: recipient has no contact details", notification.subject
        )
        return
    try:
        sender.send(notification)
    except Exception:
        logger.exception(
            "Failed to deliver %r notification to %s",
            notification.subject,
            mask_destination(notification.destination),
        )


def notify_otp(
    sender: NotificationSender,
    phone_number: str | None,
    email: str | None,
    code: str,
    ttl_seconds: int,
) -> None:
    _send(sender, messages.otp_notification(phone_number, email, code, ttl_seconds))


def notify_deposit_request(
    sender: NotificationSender,
    db: Session,
    customer_id: UUID,
    deposit_amount: float,
    deposit_link: str,
    expires_in_seconds: int,
) -> None:
    customer = db.get(UserModel, customer_id)
    if customer is None:
        return
    _send(
        sender,
        messages.deposit_request_notification(
            customer.phone_number, customer.email, deposit_amount, deposit_link, expires_in_seconds
        ),
    )


def notify_booking_cancelled(sender: NotificationSender, db: Session, customer_id: UUID) -> None:
    customer = db.get(UserModel, customer_id)
    if customer is None:
        return
    _send(sender, messages.booking_cancelled_notification(customer.phone_number, customer.email))


def notify_booking_confirmed(
    sender: NotificationSender,
    db: Session,
    customer_id: UUID,
    booking_date: str,
    start_time: str,
) -> None:
    """Sent on the channel the customer signed up with: their phone number when
    they registered by phone, their email when they came via email, Google or
    Facebook."""
    customer = db.get(UserModel, customer_id)
    if customer is None:
        return
    _send(
        sender,
        messages.booking_confirmed_notification(
            customer.phone_number, customer.email, booking_date, start_time
        ),
    )


def notify_deposit_failed(sender: NotificationSender, db: Session, customer_id: UUID) -> None:
    customer = db.get(UserModel, customer_id)
    if customer is None:
        return
    _send(sender, messages.deposit_failed_notification(customer.phone_number, customer.email))
