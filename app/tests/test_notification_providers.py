"""SMS and email delivery, without touching the real providers."""

import pytest

from app.notification.domain.notification import Notification
from app.notification.infrastructure import providers
from app.notification.infrastructure.phone import to_e164
from app.notification.infrastructure.providers import (
    DeliveryError,
    RoutingNotificationSender,
    SendGridEmailSender,
    TwilioSmsSender,
)
from app.shared.infrastructure.config.settings import settings


class FakeResponse:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("07488566218", "+447488566218"),
        ("07488 566218", "+447488566218"),
        ("+447488566218", "+447488566218"),
        ("00447488566218", "+447488566218"),
        ("01446621342", "+441446621342"),
    ],
)
def test_uk_numbers_become_e164(typed, expected):
    assert to_e164(typed, "+44") == expected


def test_twilio_sends_sms_with_e164_number(monkeypatch):
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(settings, "TWILIO_SENDER", "Nailzinc")
    monkeypatch.setattr(settings, "SMS_COUNTRY_CODE", "+44")

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(providers.requests, "post", fake_post)

    TwilioSmsSender().send(
        Notification(subject="Code", body="Your code is 123456", phone_number="07488566218")
    )

    assert "AC-test" in captured["url"]
    assert captured["data"]["To"] == "+447488566218"
    assert captured["data"]["From"] == "Nailzinc"
    assert "123456" in captured["data"]["Body"]
    assert captured["auth"] == ("AC-test", "token")


def test_twilio_failure_is_reported(monkeypatch):
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(settings, "TWILIO_SENDER", "Nailzinc")
    monkeypatch.setattr(
        providers.requests, "post", lambda url, **kw: FakeResponse(401, "unauthorised")
    )

    with pytest.raises(DeliveryError):
        TwilioSmsSender().send(Notification(subject="x", body="y", phone_number="07000000000"))


def test_sendgrid_sends_email(monkeypatch):
    monkeypatch.setattr(settings, "SENDGRID_API_KEY", "SG.key")
    monkeypatch.setattr(settings, "EMAIL_FROM_ADDRESS", "hello@nailzinc.co.uk")
    monkeypatch.setattr(settings, "EMAIL_FROM_NAME", "Nailzinc")

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(202)

    monkeypatch.setattr(providers.requests, "post", fake_post)

    SendGridEmailSender().send(
        Notification(subject="Confirm", body="code 654321", email="guest@example.com")
    )

    assert captured["headers"]["Authorization"] == "Bearer SG.key"
    body = captured["json"]
    assert body["personalizations"][0]["to"][0]["email"] == "guest@example.com"
    assert body["from"]["email"] == "hello@nailzinc.co.uk"
    assert body["subject"] == "Confirm"


class Recorder:
    def __init__(self):
        self.sent = []

    def send(self, notification):
        self.sent.append(notification)


def test_routing_prefers_the_channel_the_customer_signed_up_with():
    sms, email = Recorder(), Recorder()
    router = RoutingNotificationSender(sms, email)

    router.send(Notification(subject="a", body="b", phone_number="07000000000"))
    router.send(Notification(subject="c", body="d", email="guest@example.com"))

    assert len(sms.sent) == 1
    assert len(email.sent) == 1


def test_routing_without_a_provider_does_not_raise(caplog):
    router = RoutingNotificationSender(None, None)

    router.send(Notification(subject="a", body="b", phone_number="07000000000"))

    assert "undelivered" in caplog.text
