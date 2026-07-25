"""Single entry point for "send me a code to sign in".

Registration and login are deliberately the same operation: responding differently
for a known and an unknown phone number would let anyone probe whether a person is
a customer of the shop.
"""

import logging
import uuid
from datetime import datetime, timezone

from app.auth.application.dto import RegisterUserInput, RegisterUserOutput
from app.auth.domain.entities import User
from app.auth.domain.exceptions import OtpResendTooSoonError
from app.auth.domain.repository import OtpRepository, UserRepository
from app.auth.domain.services import generate_otp_code
from app.auth.domain.value_object import UserRole, UserStatus
from app.notification.application.notify import notify_otp
from app.notification.domain.sender import NotificationSender

logger = logging.getLogger(__name__)

OTP_TTL_SECONDS = 5 * 60
RESEND_COOLDOWN_SECONDS = 60


class RequestOtpUseCase:
    def __init__(
        self,
        user_repository: UserRepository,
        otp_repository: OtpRepository,
        notification_sender: NotificationSender,
    ):
        self._user_repository = user_repository
        self._otp_repository = otp_repository
        self._notification_sender = notification_sender

    def execute(self, input_data: RegisterUserInput) -> RegisterUserOutput:
        user = self._user_repository.find_by_identifier(input_data.phone_number, input_data.email)

        if user is None:
            user = self._user_repository.save(
                User(
                    id=uuid.uuid4(),
                    phone_number=input_data.phone_number,
                    email=input_data.email,
                    status=UserStatus.PENDING,
                    role=UserRole.CUSTOMER,
                    created_at=datetime.now(timezone.utc),
                )
            )

        if self._otp_repository.is_resend_blocked(user.id):
            raise OtpResendTooSoonError()

        # A blocked account gets the same response as any other, but no usable code:
        # verification will simply fail the way an expired code does.
        if user.status == UserStatus.BLOCKED:
            logger.warning("OTP requested for blocked user %s", user.id)
            self._otp_repository.start_resend_cooldown(user.id, RESEND_COOLDOWN_SECONDS)
            return RegisterUserOutput(pending_id=user.id, expires_in_seconds=OTP_TTL_SECONDS)

        otp_code = generate_otp_code()
        self._otp_repository.save(user.id, otp_code, OTP_TTL_SECONDS)
        self._otp_repository.start_resend_cooldown(user.id, RESEND_COOLDOWN_SECONDS)

        notify_otp(
            self._notification_sender,
            user.phone_number,
            user.email,
            otp_code,
            OTP_TTL_SECONDS,
        )

        return RegisterUserOutput(pending_id=user.id, expires_in_seconds=OTP_TTL_SECONDS)
