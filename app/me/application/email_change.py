"""Changing an email address requires proving you own it.

Without this, anyone could put someone else's address on their own account and
quietly capture that person's future Google/Facebook sign-ins, which match by
email.
"""

import hmac
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.domain.repository import OtpRepository
from app.auth.domain.services import generate_otp_code
from app.auth.infrastructure.models import UserModel
from app.notification.application.notify import notify_email_change
from app.notification.domain.sender import NotificationSender

EMAIL_CHANGE_TTL_SECONDS = 15 * 60


class EmailAlreadyUsedError(Exception):
    pass


class NoPendingEmailChangeError(Exception):
    pass


class InvalidEmailCodeError(Exception):
    pass


class EmailChangeTooManyAttemptsError(Exception):
    """Five wrong codes burn the pending change - same rule as login OTPs."""


MAX_EMAIL_CHANGE_ATTEMPTS = 5


def request_email_change(
    db: Session,
    otp_repository: OtpRepository,
    notification_sender: NotificationSender,
    user_id: UUID,
    email: str,
) -> int:
    taken = db.query(UserModel.id).filter(UserModel.email == email, UserModel.id != user_id).first()
    if taken is not None:
        raise EmailAlreadyUsedError()

    code = generate_otp_code()
    otp_repository.save_email_change(user_id, email, code, EMAIL_CHANGE_TTL_SECONDS)
    notify_email_change(notification_sender, email, code, EMAIL_CHANGE_TTL_SECONDS)
    return EMAIL_CHANGE_TTL_SECONDS


def confirm_email_change(
    db: Session, otp_repository: OtpRepository, user_id: UUID, code: str
) -> UserModel:
    pending = otp_repository.get_email_change(user_id)
    if pending is None:
        raise NoPendingEmailChangeError()

    email, expected_code = pending
    if not hmac.compare_digest(expected_code, code):
        attempts = otp_repository.register_email_change_attempt(
            user_id, EMAIL_CHANGE_TTL_SECONDS
        )
        if attempts >= MAX_EMAIL_CHANGE_ATTEMPTS:
            otp_repository.delete_email_change(user_id)
            raise EmailChangeTooManyAttemptsError()
        raise InvalidEmailCodeError()

    # Re-check at the last moment: someone else may have claimed it meanwhile.
    taken = db.query(UserModel.id).filter(UserModel.email == email, UserModel.id != user_id).first()
    if taken is not None:
        otp_repository.delete_email_change(user_id)
        raise EmailAlreadyUsedError()

    user = db.get(UserModel, user_id)
    user.email = email
    db.commit()
    db.refresh(user)
    otp_repository.delete_email_change(user_id)
    return user
