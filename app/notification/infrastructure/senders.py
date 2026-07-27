import logging

from app.notification.domain.notification import Notification, mask_destination
from app.notification.domain.sender import NotificationSender
from app.notification.infrastructure.providers import build_live_sender
from app.shared.infrastructure.config.settings import settings

logger = logging.getLogger(__name__)


class ConsoleNotificationSender(NotificationSender):
    """Prints the full message to the log. Local development only - the body of an
    OTP notification is a credential, so this must never be enabled in production."""

    def send(self, notification: Notification) -> None:
        logger.info(
            "[notification] to=%s subject=%s body=%s",
            notification.destination,
            notification.subject,
            notification.body,
        )


class NullNotificationSender(NotificationSender):
    """Records that a message was due without ever writing its contents to the log.

    This is the safe default: until a real SMS/email provider is wired up, nothing
    is delivered, but no credential leaks into log aggregation either.
    """

    def send(self, notification: Notification) -> None:
        logger.info(
            "[notification] undelivered (no provider configured) to=%s subject=%s",
            mask_destination(notification.destination),
            notification.subject,
        )


_SENDERS = {
    "console": ConsoleNotificationSender,
    "null": NullNotificationSender,
    "live": build_live_sender,
}

_factory = _SENDERS.get(settings.NOTIFICATION_BACKEND)
if _factory is None:
    logger.warning(
        "Unknown NOTIFICATION_BACKEND %r, falling back to 'null'",
        settings.NOTIFICATION_BACKEND,
    )
    _factory = NullNotificationSender

_sender: NotificationSender = _factory()


def get_notification_sender() -> NotificationSender:
    return _sender
