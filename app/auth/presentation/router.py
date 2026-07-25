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
from app.auth.application.request_otp import RequestOtpUseCase
from app.auth.application.verify_otp import VerifyOtpUseCase
from app.auth.domain.exceptions import (
    OtpExpiredError,
    OtpInvalidError,
    OtpResendTooSoonError,
    OtpTooManyAttemptsError,
    UserBlockedError,
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
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    RequestOtpRequest,
    VerifyOtpRequest,
    VerifyOtpResponse,
)
from app.notification.domain.sender import NotificationSender
from app.notification.infrastructure.senders import get_notification_sender
from app.shared.infrastructure.cache.redis_client import get_redis
from app.shared.infrastructure.config.settings import settings
from app.shared.infrastructure.database.session import get_db
from app.shared.infrastructure.rate_limit import limiter

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_STATE_TTL_SECONDS = 5 * 60


def _request_otp(
    payload: RequestOtpRequest | RegisterRequest | LoginRequest,
    db: Session,
    redis_client: Redis,
    notification_sender: NotificationSender,
) -> RegisterResponse:
    use_case = RequestOtpUseCase(
        user_repository=SqlAlchemyUserRepository(db),
        otp_repository=RedisOtpRepository(redis_client),
        notification_sender=notification_sender,
    )

    try:
        result = use_case.execute(
            RegisterUserInput(phone_number=payload.phone_number, email=payload.email)
        )
    except OtpResendTooSoonError:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="A code was sent recently. Please wait before requesting another one.",
        )

    return RegisterResponse(
        pending_id=result.pending_id, expires_in_seconds=result.expires_in_seconds
    )


@router.post(
    "/request-otp",
    response_model=RegisterResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("5/minute")
def request_otp(
    request: Request,
    payload: RequestOtpRequest,
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
    notification_sender: NotificationSender = Depends(get_notification_sender),
) -> RegisterResponse:
    """Send a one-time code, creating the account on first use.

    The response is identical whether or not the identifier is already registered.
    """
    return _request_otp(payload, db, redis_client, notification_sender)


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    deprecated=True,
)
@limiter.limit("5/minute")
def register(
    request: Request,
    payload: RegisterRequest,
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
    notification_sender: NotificationSender = Depends(get_notification_sender),
) -> RegisterResponse:
    """Deprecated alias of /auth/request-otp."""
    return _request_otp(payload, db, redis_client, notification_sender)


@router.post(
    "/login",
    response_model=RegisterResponse,
    status_code=status.HTTP_200_OK,
    deprecated=True,
)
@limiter.limit("5/minute")
def login(
    request: Request,
    payload: LoginRequest,
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
    notification_sender: NotificationSender = Depends(get_notification_sender),
) -> RegisterResponse:
    """Deprecated alias of /auth/request-otp."""
    return _request_otp(payload, db, redis_client, notification_sender)


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
    except UserBlockedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been blocked. Please contact the salon.",
        )
    except OtpExpiredError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP has expired or does not exist. Please request a new one.",
        )
    except OtpTooManyAttemptsError:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many incorrect attempts. Please request a new code.",
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
    except UserBlockedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been blocked. Please contact the salon.",
        )

    return RedirectResponse(
        f"{settings.FRONTEND_URL}/auth/callback"
        f"#token={result.access_token}&user_id={result.user_id}&role={result.role.value}"
    )
