from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.auth.domain.value_object import UserRole, UserStatus


@dataclass
class User:
    id: UUID
    phone_number: str | None
    email: str | None
    status: UserStatus
    role: UserRole
    created_at: datetime
    first_name: str | None = None
    surname: str | None = None
