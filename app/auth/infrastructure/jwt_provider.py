from datetime import datetime, timedelta, timezone
from uuid import UUID

from jose import jwt

from app.auth.domain.repository import TokenProvider
from app.auth.domain.value_object import UserRole
from app.shared.infrastructure.config.settings import settings


class JwtTokenProvider(TokenProvider):
    def create_access_token(self, user_id: UUID, role: UserRole) -> str:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
        )
        payload = {
            "sub": str(user_id),
            "user_id": str(user_id),
            "role": role.value,
            "exp": expire,
        }
        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
