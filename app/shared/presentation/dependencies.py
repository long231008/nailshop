from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.shared.infrastructure.config.settings import settings
from app.shared.infrastructure.database.session import get_db

security = HTTPBearer()

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired token",
    headers={"WWW-Authenticate": "Bearer"},
)


class CurrentUser(BaseModel):
    id: UUID
    role: UserRole


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> CurrentUser:
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        user_id = payload.get("user_id")
        if user_id is None:
            raise ValueError("missing claims")
        user_id = UUID(user_id)
    except (JWTError, ValueError):
        raise _UNAUTHORIZED

    # The token says who you were when it was issued; the database says who you are
    # now. Blocking an account or changing its role has to take effect immediately,
    # so the role is always read back from the database rather than from the claims.
    user = db.get(UserModel, user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise _UNAUTHORIZED

    return CurrentUser(id=user.id, role=user.role)


def require_roles(*roles: UserRole):
    allowed = set(roles) | {UserRole.ADMIN}

    def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )
        return current_user

    return dependency
