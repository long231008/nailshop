import logging
import uuid
from datetime import datetime, timezone

from app.auth.application.dto import RegisterUserInput, RegisterUserOutput
from app.auth.domain.entities import User
from app.auth.domain.exceptions import UserAlreadyActiveError
from app.auth.domain.repository import OtpRepository, UserRepository
from app.auth.domain.services import generate_otp_code
from app.auth.domain.value_object import UserRole, UserStatus

logger = logging.getLogger(__name__)

OTP_TTL_SECONDS = 5 * 60


class RegisterUserUseCase:
    def __init__(self, user_repository: UserRepository, otp_repository: OtpRepository):
        self._user_repository = user_repository
        self._otp_repository = otp_repository

    def execute(self, input_data: RegisterUserInput) -> RegisterUserOutput:
        existing = self._user_repository.find_by_identifier(
            input_data.phone_number, input_data.email
        )

        if existing is not None and existing.status == UserStatus.ACTIVE:
            raise UserAlreadyActiveError()

        if existing is not None:
            user = existing
        else:
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

        otp_code = generate_otp_code()
        self._otp_repository.save(user.id, otp_code, OTP_TTL_SECONDS)

        logger.info("OTP for pending_id=%s: %s (ttl=%ss)", user.id, otp_code, OTP_TTL_SECONDS)

        return RegisterUserOutput(pending_id=user.id, expires_in_seconds=OTP_TTL_SECONDS)
