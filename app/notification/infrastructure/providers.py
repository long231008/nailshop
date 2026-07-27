"""Real delivery: SMS through Twilio, email through SendGrid.

Both are plain REST calls made with `requests`, which the project already
depends on - no extra SDKs to keep up to date.
"""

import logging

import requests

from app.notification.domain.notification import Notification, mask_destination
from app.notification.domain.sender import NotificationSender
from app.notification.infrastructure.phone import to_e164
from app.shared.infrastructure.config.settings import settings

logger = logging.getLogger(__name__)

TWILIO_ENDPOINT = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
SENDGRID_ENDPOINT = "https://api.sendgrid.com/v3/mail/send"
TIMEOUT_SECONDS = 10


class DeliveryError(Exception):
    pass


def twilio_configured() -> bool:
    return bool(
        settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_SENDER
    )


def sendgrid_configured() -> bool:
    return bool(settings.SENDGRID_API_KEY and settings.EMAIL_FROM_ADDRESS)


class TwilioSmsSender(NotificationSender):
    def send(self, notification: Notification) -> None:
        if not notification.phone_number:
            raise DeliveryError("no phone number on this notification")

        response = requests.post(
            TWILIO_ENDPOINT.format(sid=settings.TWILIO_ACCOUNT_SID),
            auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            data={
                "From": settings.TWILIO_SENDER,
                "To": to_e164(notification.phone_number),
                "Body": notification.body,
            },
            timeout=TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            raise DeliveryError(f"Twilio returned {response.status_code}: {response.text[:200]}")
        logger.info("SMS sent to %s", mask_destination(notification.phone_number))


class SendGridEmailSender(NotificationSender):
    def send(self, notification: Notification) -> None:
        if not notification.email:
            raise DeliveryError("no email address on this notification")

        response = requests.post(
            SENDGRID_ENDPOINT,
            headers={"Authorization": f"Bearer {settings.SENDGRID_API_KEY}"},
            json={
                "personalizations": [{"to": [{"email": notification.email}]}],
                "from": {
                    "email": settings.EMAIL_FROM_ADDRESS,
                    "name": settings.EMAIL_FROM_NAME,
                },
                "subject": notification.subject,
                "content": [{"type": "text/plain", "value": notification.body}],
            },
            timeout=TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            raise DeliveryError(f"SendGrid returned {response.status_code}: {response.text[:200]}")
        logger.info("Email sent to %s", mask_destination(notification.email))


class RoutingNotificationSender(NotificationSender):
    """Reaches people on the channel they signed up with.

    Customers register with either a phone number or an email address, so the
    notification itself says which way to go. Anything without a configured
    provider is logged as undelivered rather than silently dropped.
    """

    def __init__(self, sms: NotificationSender | None, email: NotificationSender | None):
        self._sms = sms
        self._email = email

    def send(self, notification: Notification) -> None:
        if notification.phone_number and self._sms is not None:
            self._sms.send(notification)
            return
        if notification.email and self._email is not None:
            self._email.send(notification)
            return

        logger.warning(
            "[notification] undelivered (no provider for this channel) to=%s subject=%s",
            mask_destination(notification.destination),
            notification.subject,
        )


def build_live_sender() -> NotificationSender:
    sms = TwilioSmsSender() if twilio_configured() else None
    email = SendGridEmailSender() if sendgrid_configured() else None

    if sms is None:
        logger.warning("NOTIFICATION_BACKEND=live but Twilio is not configured: no SMS will go out")
    if email is None:
        logger.warning(
            "NOTIFICATION_BACKEND=live but SendGrid is not configured: no email will go out"
        )

    return RoutingNotificationSender(sms, email)
