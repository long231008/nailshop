from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.auth.domain.value_object import UserStatus


@dataclass
class User:
    id: UUID
    phone_number: str | None
    email: str | None
    status: UserStatus
    created_at: datetime
