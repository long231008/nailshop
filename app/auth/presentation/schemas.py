from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator

PHONE_PATTERN = r"^\+?[0-9]{6,15}$"


class RequestOtpRequest(BaseModel):
    phone_number: str | None = Field(default=None, pattern=PHONE_PATTERN, examples=["0901234567"])
    email: EmailStr | None = None

    @model_validator(mode="after")
    def require_identifier(self) -> "RequestOtpRequest":
        if not self.phone_number and not self.email:
            raise ValueError("phone_number or email is required")
        if self.phone_number and self.email:
            raise ValueError("provide either phone_number or email, not both")
        return self


class RegisterRequest(RequestOtpRequest):
    pass


class LoginRequest(RequestOtpRequest):
    pass


class RegisterResponse(BaseModel):
    pending_id: UUID
    expires_in_seconds: int


class VerifyOtpRequest(BaseModel):
    pending_id: UUID
    otp_code: str = Field(pattern=r"^\d{6}$", examples=["123456"])


class VerifyOtpResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: UUID
    role: str
