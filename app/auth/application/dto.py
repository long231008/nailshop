from dataclasses import dataclass
from uuid import UUID

from app.auth.domain.value_object import UserRole


@dataclass
class RegisterUserInput:
    phone_number: str | None
    email: str | None


@dataclass
class RegisterUserOutput:
    pending_id: UUID
    expires_in_seconds: int


@dataclass
class VerifyOtpInput:
    pending_id: UUID
    otp_code: str


@dataclass
class VerifyOtpOutput:
    access_token: str
    token_type: str
    user_id: UUID
    role: UserRole
