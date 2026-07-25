import logging

from app.auth.application.dto import RegisterUserInput, RegisterUserOutput
from app.auth.domain.exceptions import UserNotFoundError
from app.auth.domain.repository import OtpRepository, UserRepository
from app.auth.domain.services import generate_otp_code

logger = logging.getLogger(__name__)

OTP_TTL_SECONDS = 5 * 60


class LoginUseCase:
    def __init__(self, user_repository: UserRepository, otp_repository: OtpRepository):
        self._user_repository = user_repository
        self._otp_repository = otp_repository

    def execute(self, input_data: RegisterUserInput) -> RegisterUserOutput:
        user = self._user_repository.find_by_identifier(input_data.phone_number, input_data.email)
        if user is None:
            raise UserNotFoundError()

        otp_code = generate_otp_code()
        self._otp_repository.save(user.id, otp_code, OTP_TTL_SECONDS)

        logger.info("Login OTP for pending_id=%s: %s (ttl=%ss)", user.id, otp_code, OTP_TTL_SECONDS)

        return RegisterUserOutput(pending_id=user.id, expires_in_seconds=OTP_TTL_SECONDS)
