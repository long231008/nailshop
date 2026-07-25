from app.auth.application.dto import VerifyOtpInput, VerifyOtpOutput
from app.auth.domain.exceptions import OtpExpiredError, OtpInvalidError, UserNotFoundError
from app.auth.domain.repository import OtpRepository, TokenProvider, UserRepository
from app.auth.domain.value_object import UserStatus


class VerifyOtpUseCase:
    def __init__(
        self,
        user_repository: UserRepository,
        otp_repository: OtpRepository,
        token_provider: TokenProvider,
    ):
        self._user_repository = user_repository
        self._otp_repository = otp_repository
        self._token_provider = token_provider

    def execute(self, input_data: VerifyOtpInput) -> VerifyOtpOutput:
        user = self._user_repository.get_by_id(input_data.pending_id)
        if user is None:
            raise UserNotFoundError()

        stored_code = self._otp_repository.get(input_data.pending_id)
        if stored_code is None:
            raise OtpExpiredError()

        if stored_code != input_data.otp_code:
            raise OtpInvalidError()

        self._otp_repository.delete(input_data.pending_id)

        user.status = UserStatus.ACTIVE
        user = self._user_repository.update(user)

        access_token = self._token_provider.create_access_token(user_id=user.id, role=user.role)

        return VerifyOtpOutput(
            access_token=access_token,
            token_type="bearer",
            user_id=user.id,
            role=user.role,
        )
