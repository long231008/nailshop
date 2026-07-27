"""Real delivery: SMS through Twilio, email through SendGrid or plain SMTP.

The HTTP providers are called with `requests`, which the project already depends
on, and SMTP uses the standard library - no extra SDKs to keep up to date.
Email has a free path (SMTP against a free tier); SMS does not, because carriers
charge per message, so email is preferred when a customer has both.
"""

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

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


class SmtpEmailSender(NotificationSender):
    """Email over plain SMTP.

    Works with any provider that hands out SMTP credentials - including the free
    tiers of Brevo, Resend and Mailgun, or a Gmail app password - so the salon
    can send email without a paid plan.
    """

    def send(self, notification: Notification) -> None:
        if not notification.email:
            raise DeliveryError("no email address on this notification")

        message = EmailMessage()
        message["Subject"] = notification.subject
        message["From"] = formataddr((settings.EMAIL_FROM_NAME, settings.EMAIL_FROM_ADDRESS))
        message["To"] = notification.email
        message.set_content(notification.body)

        try:
            with smtplib.SMTP(
                settings.SMTP_HOST, settings.SMTP_PORT, timeout=TIMEOUT_SECONDS
            ) as server:
                if settings.SMTP_USE_TLS:
                    server.starttls()
                if settings.SMTP_USERNAME:
                    server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                server.send_message(message)
        except smtplib.SMTPException as exc:
            raise DeliveryError(f"SMTP delivery failed: {exc}") from exc

        logger.info("Email sent to %s", mask_destination(notification.email))


def smtp_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.EMAIL_FROM_ADDRESS)


class RoutingNotificationSender(NotificationSender):
    """Reaches people on the channel they signed up with.

    Customers register with either a phone number or an email address, so the
    notification itself says which way to go. When both are known, email wins by
    default: it is free where SMS is charged per message. Anything without a
    configured provider is logged as undelivered rather than silently dropped.
    """

    def __init__(
        self,
        sms: NotificationSender | None,
        email: NotificationSender | None,
        prefer_email: bool | None = None,
    ):
        self._sms = sms
        self._email = email
        self._prefer_email = (
            settings.PREFER_EMAIL_OVER_SMS if prefer_email is None else prefer_email
        )

    def send(self, notification: Notification) -> None:
        can_sms = bool(notification.phone_number) and self._sms is not None
        can_email = bool(notification.email) and self._email is not None

        if can_email and (self._prefer_email or not can_sms):
            self._email.send(notification)
            return
        if can_sms:
            self._sms.send(notification)
            return

        logger.warning(
            "[notification] undelivered (no provider for this channel) to=%s subject=%s",
            mask_destination(notification.destination),
            notification.subject,
        )


def build_live_sender() -> NotificationSender:
    sms = TwilioSmsSender() if twilio_configured() else None

    # SendGrid's API when there is a key, otherwise SMTP - which covers the
    # free providers.
    if sendgrid_configured():
        email = SendGridEmailSender()
    elif smtp_configured():
        email = SmtpEmailSender()
    else:
        email = None

    if sms is None:
        logger.warning("NOTIFICATION_BACKEND=live without Twilio: no SMS will go out")
    if email is None:
        logger.warning("NOTIFICATION_BACKEND=live without SendGrid or SMTP: no email will go out")

    return RoutingNotificationSender(sms, email)
