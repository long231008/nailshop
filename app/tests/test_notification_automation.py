from app.notification.application import notify
from app.notification.domain.notification import Notification, mask_destination
from app.notification.infrastructure.senders import (
    ConsoleNotificationSender,
    NullNotificationSender,
)


def test_mask_destination_phone():
    assert mask_destination("0912345678") == "***5678"
    assert mask_destination("123") == "***"


def test_mask_destination_email():
    assert mask_destination("customer@example.com") == "cu***@example.com"
    assert mask_destination("a@b.com") == "a***@b.com"


class FailingNotificationSender:
    def send(self, notification: Notification) -> None:
        raise RuntimeError("Network error sending notification")


def test_notification_delivery_resilience():
    # Sending notification with a failing sender should log exception but never raise
    sender = FailingNotificationSender()
    notify.notify_otp(sender, "0912345678", None, "123456", 300)


def test_console_and_null_senders(caplog):
    console_sender = ConsoleNotificationSender()
    null_sender = NullNotificationSender()

    n = Notification(phone_number="0912345678", subject="Test", body="Hello World")

    with caplog.at_level("INFO"):
        console_sender.send(n)
        assert "Hello World" in caplog.text

    caplog.clear()

    with caplog.at_level("INFO"):
        null_sender.send(n)
        assert "undelivered" in caplog.text
        assert "***5678" in caplog.text
        assert "Hello World" not in caplog.text


def test_notify_otp_skips_when_no_destination():
    class MockSender:
        called = False

        def send(self, notification: Notification) -> None:
            self.called = True

    sender = MockSender()
    notify.notify_otp(sender, None, None, "123456", 300)
    assert not sender.called
