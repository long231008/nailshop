from fastapi import APIRouter, Depends, HTTPException, status
from redis import Redis
from sqlalchemy.orm import Session

from app.auth.application.dto import RegisterUserInput
from app.auth.application.register_user import RegisterUserUseCase
from app.auth.domain.exceptions import UserAlreadyActiveError
from app.auth.infrastructure.otp_repository_impl import RedisOtpRepository
from app.auth.infrastructure.repository_impl import SqlAlchemyUserRepository
from app.auth.presentation.schemas import RegisterRequest, RegisterResponse
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
            detail="Tài khoản đã tồn tại và đang hoạt động",
        )

    return RegisterResponse(
        pending_id=result.pending_id, expires_in_seconds=result.expires_in_seconds
    )
