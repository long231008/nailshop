from abc import ABC, abstractmethod
from uuid import UUID

from app.auth.domain.entities import User
from app.auth.domain.value_object import UserRole


class UserRepository(ABC):
    @abstractmethod
    def find_by_identifier(self, phone_number: str | None, email: str | None) -> User | None:
        raise NotImplementedError

    @abstractmethod
    def get_by_id(self, user_id: UUID) -> User | None:
        raise NotImplementedError

    @abstractmethod
    def save(self, user: User) -> User:
        raise NotImplementedError

    @abstractmethod
    def update(self, user: User) -> User:
        raise NotImplementedError


class OtpRepository(ABC):
    @abstractmethod
    def save(self, pending_id: UUID, code: str, ttl_seconds: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def get(self, pending_id: UUID) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, pending_id: UUID) -> None:
        raise NotImplementedError

    @abstractmethod
    def register_failed_attempt(self, pending_id: UUID, ttl_seconds: int) -> int:
        """Count one wrong guess and return how many have been made for this code."""
        raise NotImplementedError

    @abstractmethod
    def start_resend_cooldown(self, pending_id: UUID, ttl_seconds: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_resend_blocked(self, pending_id: UUID) -> bool:
        raise NotImplementedError


class TokenProvider(ABC):
    @abstractmethod
    def create_access_token(self, user_id: UUID, role: UserRole) -> str:
        raise NotImplementedError
