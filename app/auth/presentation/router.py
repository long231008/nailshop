import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from redis import Redis
from sqlalchemy.orm import Session

from app.auth.application.dto import RegisterUserInput, VerifyOtpInput
from app.auth.application.google_login import (
    GoogleEmailNotVerifiedError,
    login_or_register_with_google,
)
from app.auth.application.register_user import RegisterUserUseCase
from app.auth.application.verify_otp import VerifyOtpUseCase
from app.auth.domain.exceptions import (
    OtpExpiredError,
    OtpInvalidError,
    UserAlreadyActiveError,
    UserNotFoundError,
)
from app.auth.infrastructure.google_oauth import (
    GoogleTokenExchangeError,
    InvalidGoogleTokenError,
    build_authorization_url,
    exchange_code_for_id_token,
    verify_id_token,
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
from app.shared.infrastructure.config.settings import settings
from app.shared.infrastructure.database.session import get_db
from app.shared.infrastructure.rate_limit import limiter

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_STATE_TTL_SECONDS = 5 * 60


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("5/minute")
def register(
    request: Request,
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
    "/verify",
    response_model=VerifyOtpResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("10/minute")
def verify_otp(
    request: Request,
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


@router.get("/google/login")
def google_login(redis_client: Redis = Depends(get_redis)) -> RedirectResponse:
    state = secrets.token_urlsafe(24)
    redis_client.set(f"oauth:google:state:{state}", "1", ex=GOOGLE_STATE_TTL_SECONDS)
    return RedirectResponse(build_authorization_url(state))


@router.get("/google/callback")
def google_callback(
    code: str,
    state: str,
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
) -> RedirectResponse:
    state_key = f"oauth:google:state:{state}"
    if redis_client.get(state_key) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state",
        )
    redis_client.delete(state_key)

    try:
        id_token_str = exchange_code_for_id_token(code)
        claims = verify_id_token(id_token_str)
    except (GoogleTokenExchangeError, InvalidGoogleTokenError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google authentication failed",
        )

    try:
        result = login_or_register_with_google(
            user_repository=SqlAlchemyUserRepository(db),
            token_provider=JwtTokenProvider(),
            email=claims.get("email"),
            email_verified=claims.get("email_verified", False),
        )
    except GoogleEmailNotVerifiedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google account has no verified email",
        )

    return RedirectResponse(
        f"{settings.FRONTEND_URL}/auth/callback"
        f"#token={result.access_token}&user_id={result.user_id}&role={result.role.value}"
    )
