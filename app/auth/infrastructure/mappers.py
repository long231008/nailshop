from app.auth.domain.entities import User
from app.auth.infrastructure.models import UserModel


class UserMapper:
    @staticmethod
    def to_entity(model: UserModel) -> User:
        return User(
            id=model.id,
            phone_number=model.phone_number,
            email=model.email,
            status=model.status,
            created_at=model.created_at,
        )

    @staticmethod
    def to_model(entity: User) -> UserModel:
        return UserModel(
            id=entity.id,
            phone_number=entity.phone_number,
            email=entity.email,
            status=entity.status,
            created_at=entity.created_at,
        )
