from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator


class RegisterRequest(BaseModel):
    phone_number: str | None = Field(default=None, examples=["0901234567"])
    email: EmailStr | None = None

    @model_validator(mode="after")
    def require_identifier(self) -> "RegisterRequest":
        if not self.phone_number and not self.email:
            raise ValueError("phone_number hoặc email là bắt buộc")
        return self


class RegisterResponse(BaseModel):
    pending_id: UUID
    expires_in_seconds: int
