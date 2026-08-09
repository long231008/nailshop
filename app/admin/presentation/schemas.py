from datetime import date, datetime
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
    # Home branch preference only - technicians belong to the chain.
    branch_id: UUID | None = None
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
    branch_id: UUID | None
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


class UserPhoneUpdateRequest(BaseModel):
    # Same shape registration accepts, so a corrected number can log in.
    phone_number: str = Field(pattern=r"^\+?[0-9]{6,15}$", examples=["07412856241"])


class UserAdminResponse(BaseModel):
    id: UUID
    phone_number: str | None
    email: str | None
    role: str
    status: str


class UserListItem(BaseModel):
    id: UUID
    phone_number: str | None
    email: str | None
    first_name: str | None
    surname: str | None
    role: str
    status: str
    created_at: datetime
    booking_count: int = 0
    # Present when the user already has a staff profile.
    staff_id: UUID | None = None
    staff_branch_id: UUID | None = None
    staff_display_name: str | None = None
    staff_status: str | None = None


class UserBookingDetailItem(BaseModel):
    service_name: str
    staff_name: str | None
    start_time: datetime
    end_time: datetime
    price: float
    status: str


class UserBookingItem(BaseModel):
    id: UUID
    branch_id: UUID
    booking_date: date
    status: str
    total_price: float | None
    deposit_amount: float | None
    created_at: datetime
    details: list[UserBookingDetailItem]


class GrantStaffRequest(BaseModel):
    # Home branch preference only - technicians belong to the chain.
    branch_id: UUID | None = None
    display_name: str = Field(min_length=1, max_length=255)


class UserDeleteResponse(BaseModel):
    id: UUID
    # "deleted": the row is gone. "anonymized": the account had booking/payment
    # history, so personal data was stripped and login blocked instead.
    mode: str
