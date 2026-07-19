from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth.domain.entities import User
from app.auth.domain.repository import UserRepository
from app.auth.infrastructure.mappers import UserMapper
from app.auth.infrastructure.models import UserModel


class SqlAlchemyUserRepository(UserRepository):
    def __init__(self, db: Session):
        self._db = db

    def find_by_identifier(
        self, phone_number: str | None, email: str | None
    ) -> User | None:
        conditions = []
        if phone_number:
            conditions.append(UserModel.phone_number == phone_number)
        if email:
            conditions.append(UserModel.email == email)

        if not conditions:
            return None

        model = self._db.query(UserModel).filter(or_(*conditions)).first()
        return UserMapper.to_entity(model) if model else None

    def save(self, user: User) -> User:
        model = UserMapper.to_model(user)
        self._db.add(model)
        self._db.commit()
        self._db.refresh(model)
        return UserMapper.to_entity(model)
