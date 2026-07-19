from fastapi import APIRouter, Depends, HTTPException, status
from redis import Redis
from sqlalchemy.orm import Session

from app.auth.application.dto import RegisterUserInput, VerifyOtpInput
from app.auth.application.register_user import RegisterUserUseCase
from app.auth.application.verify_otp import VerifyOtpUseCase
from app.auth.domain.exceptions import (
    OtpExpiredError,
    OtpInvalidError,
    UserAlreadyActiveError,
    UserNotFoundError,
)
from app.auth.infrastructure.jwt_provider import JwtTokenProvider
from app.auth.infrastructure.otp_repository_impl import RedisOtpRepository
from app.auth.infrastructure.repository_impl import SqlAlchemyUserRepository
from app.auth.presentation.schemas import (
    RegisterRequest,
    RegisterResponse,
    VerifyOtpRequest,
    VerifyOtpResponse,
)
from app.shared.infrastructure.cache.redis_client import get_redis
from app.shared.infrastructure.database.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
) -> RegisterResponse:
    use_case = RegisterUserUseCase(
        user_repository=SqlAlchemyUserRepository(db),
        otp_repository=RedisOtpRepository(redis_client),
    )

    try:
        result = use_case.execute(
            RegisterUserInput(phone_number=payload.phone_number, email=payload.email)
        )
    except UserAlreadyActiveError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this identifier is already active",
        )

    return RegisterResponse(
        pending_id=result.pending_id, expires_in_seconds=result.expires_in_seconds
    )


@router.post(
    "/verify-otp",
    response_model=VerifyOtpResponse,
    status_code=status.HTTP_200_OK,
)
def verify_otp(
    payload: VerifyOtpRequest,
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
) -> VerifyOtpResponse:
    use_case = VerifyOtpUseCase(
        user_repository=SqlAlchemyUserRepository(db),
        otp_repository=RedisOtpRepository(redis_client),
        token_provider=JwtTokenProvider(),
    )

    try:
        result = use_case.execute(
            VerifyOtpInput(pending_id=payload.pending_id, otp_code=payload.otp_code)
        )
    except UserNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending registration not found",
        )
    except OtpExpiredError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP has expired or does not exist. Please request a new one.",
        )
    except OtpInvalidError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OTP code",
        )

    return VerifyOtpResponse(
        access_token=result.access_token,
        token_type=result.token_type,
        user_id=result.user_id,
        role=result.role.value,
    )
