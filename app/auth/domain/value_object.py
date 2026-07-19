from enum import Enum


class UserStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"


class UserRole(str, Enum):
    CUSTOMER = "customer"
    STAFF = "staff"
    ADMIN = "admin"
