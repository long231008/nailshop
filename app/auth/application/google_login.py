import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app.auth.domain.entities import User
from app.auth.domain.repository import TokenProvider, UserRepository
from app.auth.domain.value_object import UserRole, UserStatus


class GoogleEmailNotVerifiedError(Exception):
    pass


@dataclass
class GoogleLoginOutput:
    access_token: str
    token_type: str
    user_id: uuid.UUID
    role: UserRole


def login_or_register_with_google(
    user_repository: UserRepository,
    token_provider: TokenProvider,
    email: str | None,
    email_verified: bool,
) -> GoogleLoginOutput:
    if not email or not email_verified:
        raise GoogleEmailNotVerifiedError()

    user = user_repository.find_by_identifier(phone_number=None, email=email)

    if user is None:
        user = user_repository.save(
            User(
                id=uuid.uuid4(),
                phone_number=None,
                email=email,
                status=UserStatus.ACTIVE,
                role=UserRole.CUSTOMER,
                created_at=datetime.now(timezone.utc),
            )
        )
    elif user.status != UserStatus.ACTIVE:
        user.status = UserStatus.ACTIVE
        user = user_repository.update(user)

    access_token = token_provider.create_access_token(user_id=user.id, role=user.role)

    return GoogleLoginOutput(
        access_token=access_token, token_type="bearer", user_id=user.id, role=user.role
    )
