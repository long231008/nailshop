from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator

from app.auth.domain.value_object import UserRole, UserStatus


class StaffActionResponse(BaseModel):
    staff_id: UUID
    status: str
    reassigned_bookings: int = 0


class DesignPricingPermissionRequest(BaseModel):
    enabled: bool


class DesignPricingPermissionResponse(BaseModel):
    staff_id: UUID
    can_price_custom_designs: bool


class SlotLockPermissionRequest(BaseModel):
    enabled: bool


class SlotLockPermissionResponse(BaseModel):
    staff_id: UUID
    can_lock_slots: bool


class StaffCreateRequest(BaseModel):
    branch_id: UUID
    display_name: str = Field(min_length=1, max_length=255)
    # Either attach an existing user, or create one from a phone number/email.
    user_id: UUID | None = None
    phone_number: str | None = Field(default=None, pattern=r"^\+?[0-9]{6,15}$")
    email: EmailStr | None = None

    @model_validator(mode="after")
    def require_identity(self) -> "StaffCreateRequest":
        if self.user_id is None and not self.phone_number and not self.email:
            raise ValueError("user_id, phone_number or email is required")
        return self


class StaffCreatedResponse(BaseModel):
    staff_id: UUID
    user_id: UUID
    branch_id: UUID
    display_name: str
    status: str


class UserUpdateRequest(BaseModel):
    role: UserRole | None = None
    status: UserStatus | None = None

    @model_validator(mode="after")
    def require_change(self) -> "UserUpdateRequest":
        if self.role is None and self.status is None:
            raise ValueError("role or status is required")
        return self


class UserAdminResponse(BaseModel):
    id: UUID
    phone_number: str | None
    email: str | None
    role: str
    status: str
