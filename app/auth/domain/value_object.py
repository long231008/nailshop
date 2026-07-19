from enum import Enum


class UserStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
