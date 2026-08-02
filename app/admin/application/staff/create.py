from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole, UserStatus
from app.auth.infrastructure.models import UserModel
from app.branches.infrastructure.models import LocationModel
from app.staff.infrastructure.models import StaffModel


class BranchNotFoundError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


class AlreadyStaffError(Exception):
    pass


def create_staff(
    db: Session,
    branch_id: UUID | None,
    display_name: str,
    user_id: UUID | None,
    phone_number: str | None,
    email: str | None,
) -> StaffModel:
    # Technicians belong to the chain; the branch is only their optional home
    # preference - each day's salon is decided by the nightly Step A.
    if branch_id is not None and db.get(LocationModel, branch_id) is None:
        raise BranchNotFoundError()

    if user_id is not None:
        user = db.get(UserModel, user_id)
        if user is None:
            raise UserNotFoundError()
    else:
        user = (
            db.query(UserModel)
            .filter(
                (UserModel.phone_number == phone_number)
                if phone_number
                else (UserModel.email == email)
            )
            .first()
        )
        if user is None:
            user = UserModel(
                phone_number=phone_number,
                email=email,
                status=UserStatus.ACTIVE,
                role=UserRole.STAFF,
            )
            db.add(user)
            db.flush()

    existing = db.query(StaffModel).filter(StaffModel.user_id == user.id).first()
    if existing is not None:
        raise AlreadyStaffError()

    if user.role == UserRole.CUSTOMER:
        user.role = UserRole.STAFF

    staff = StaffModel(user_id=user.id, branch_id=branch_id, display_name=display_name)
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff
