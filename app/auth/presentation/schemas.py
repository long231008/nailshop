from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator


class RegisterRequest(BaseModel):
    phone_number: str | None = Field(default=None, examples=["0901234567"])
    email: EmailStr | None = None

    @model_validator(mode="after")
    def require_identifier(self) -> "RegisterRequest":
        if not self.phone_number and not self.email:
            raise ValueError("phone_number or email is required")
        return self


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
