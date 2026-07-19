from abc import ABC, abstractmethod
from uuid import UUID

from app.auth.domain.entities import User


class UserRepository(ABC):
    @abstractmethod
    def find_by_identifier(
        self, phone_number: str | None, email: str | None
    ) -> User | None:
        raise NotImplementedError

    @abstractmethod
    def save(self, user: User) -> User:
        raise NotImplementedError


class OtpRepository(ABC):
    @abstractmethod
    def save(self, pending_id: UUID, code: str, ttl_seconds: int) -> None:
        raise NotImplementedError
