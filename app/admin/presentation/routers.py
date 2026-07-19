from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.admin.application.staff.block import block_staff
from app.admin.application.staff.reserve import StaffNotFoundError, reserve_staff
from app.admin.presentation.schemas import StaffActionResponse
from app.auth.domain.value_object import UserRole
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import CurrentUser, require_roles

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/staff/{staff_id}/reserve", response_model=StaffActionResponse)
def reserve_staff_endpoint(
    staff_id: UUID,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> StaffActionResponse:
    try:
        staff = reserve_staff(db, staff_id)
    except StaffNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")

    return StaffActionResponse(staff_id=staff.id, status=staff.status.value)


@router.post("/staff/{staff_id}/block", response_model=StaffActionResponse)
def block_staff_endpoint(
    staff_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.ADMIN)),
) -> StaffActionResponse:
    try:
        staff, reassigned = block_staff(db, staff_id, current_user.id)
    except StaffNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")

    return StaffActionResponse(
        staff_id=staff.id, status=staff.status.value, reassigned_bookings=reassigned
    )
