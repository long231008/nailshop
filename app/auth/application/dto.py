from dataclasses import dataclass
from uuid import UUID


@dataclass
class RegisterUserInput:
    phone_number: str | None
    email: str | None


@dataclass
class RegisterUserOutput:
    pending_id: UUID
    expires_in_seconds: int
