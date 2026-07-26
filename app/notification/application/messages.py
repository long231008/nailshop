"""Builders for the messages we send to customers.

All customer-facing copy is English - it is text that appears "on the web".
"""

from app.notification.domain.notification import Notification


def otp_notification(
    phone_number: str | None, email: str | None, code: str, ttl_seconds: int
) -> Notification:
    minutes = max(1, ttl_seconds // 60)
    return Notification(
        subject="Your verification code",
        body=(
            f"Your nailshop verification code is {code}. "
            f"It expires in {minutes} minutes. Do not share this code with anyone."
        ),
        phone_number=phone_number,
        email=email,
    )


def deposit_request_notification(
    phone_number: str | None,
    email: str | None,
    deposit_amount: float,
    deposit_link: str,
    expires_in_seconds: int,
) -> Notification:
    minutes = max(1, expires_in_seconds // 60)
    return Notification(
        subject="Your booking is approved - deposit required",
        body=(
            f"Your booking has been approved. Please pay the £{deposit_amount:.2f} deposit "
            f"within {minutes} minutes to secure your slot: {deposit_link} "
            "Your slot is released automatically if the deposit is not received in time."
        ),
        phone_number=phone_number,
        email=email,
    )


def booking_confirmed_notification(
    phone_number: str | None, email: str | None, booking_date: str, start_time: str
) -> Notification:
    return Notification(
        subject="Your booking is confirmed",
        body=(
            f"Thank you - your deposit has been received and your appointment on "
            f"{booking_date} at {start_time} is confirmed. We look forward to seeing you!"
        ),
        phone_number=phone_number,
        email=email,
    )


def deposit_failed_notification(phone_number: str | None, email: str | None) -> Notification:
    return Notification(
        subject="Your deposit payment failed",
        body=(
            "Unfortunately your deposit payment did not go through, so your booking "
            "is not confirmed yet. Please try paying again from your account page, "
            "or contact the salon if the problem continues."
        ),
        phone_number=phone_number,
        email=email,
    )


def email_change_notification(email: str, code: str, ttl_seconds: int) -> Notification:
    minutes = max(1, ttl_seconds // 60)
    return Notification(
        subject="Confirm your new email address",
        body=(
            f"Use code {code} to confirm this email address on your nailshop account. "
            f"It expires in {minutes} minutes. If you did not request this, ignore this message."
        ),
        # Deliberately sent to the NEW address: that is what proves ownership.
        email=email,
    )


def design_priced_notification(
    phone_number: str | None, email: str | None, price: float
) -> Notification:
    return Notification(
        subject="Your nail design has been priced",
        body=(
            f"Good news - our team has looked at your design and quoted £{price:.2f}. "
            "Log in to your account to accept the quote and book your appointment."
        ),
        phone_number=phone_number,
        email=email,
    )


def booking_cancelled_notification(phone_number: str | None, email: str | None) -> Notification:
    return Notification(
        subject="Your booking was released",
        body=(
            "Your booking was released because the deposit was not received in time. "
            "You are welcome to book another slot at any time."
        ),
        phone_number=phone_number,
        email=email,
    )
