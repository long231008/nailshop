from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.admin.application.staff.activate import activate_staff
from app.admin.application.staff.block import block_staff
from app.admin.application.staff.create import (
    AlreadyStaffError,
    BranchNotFoundError,
    UserNotFoundError,
    create_staff,
)
from app.admin.application.staff.design_pricing import set_design_pricing_permission
from app.admin.application.staff.reserve import StaffNotFoundError, reserve_staff
from app.admin.presentation.schemas import (
    DesignPricingPermissionRequest,
    DesignPricingPermissionResponse,
    SlotLockPermissionRequest,
    SlotLockPermissionResponse,
    StaffActionResponse,
    StaffCreatedResponse,
    StaffCreateRequest,
    UserAdminResponse,
    UserUpdateRequest,
)
from app.audit_log.infrastructure.models import AuditLogModel
from app.auth.domain.value_object import UserRole
from app.auth.infrastructure.models import UserModel
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import CurrentUser, require_roles
from app.staff.infrastructure.models import StaffModel

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/staff", response_model=StaffCreatedResponse, status_code=status.HTTP_201_CREATED)
def create_staff_endpoint(
    payload: StaffCreateRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.ADMIN)),
) -> StaffCreatedResponse:
    try:
        staff = create_staff(
            db,
            branch_id=payload.branch_id,
            display_name=payload.display_name,
            user_id=payload.user_id,
            phone_number=payload.phone_number,
            email=payload.email,
        )
    except BranchNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")
    except UserNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    except AlreadyStaffError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This user is already a staff member",
        )

    db.add(
        AuditLogModel(
            actor_user_id=current_user.id,
            action="staff.created",
            entity_type="staff",
            entity_id=staff.id,
        )
    )
    db.commit()

    return StaffCreatedResponse(
        staff_id=staff.id,
        user_id=staff.user_id,
        branch_id=staff.branch_id,
        display_name=staff.display_name,
        status=staff.status.value,
    )


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


@router.post("/staff/{staff_id}/activate", response_model=StaffActionResponse)
def activate_staff_endpoint(
    staff_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.ADMIN)),
) -> StaffActionResponse:
    try:
        staff = activate_staff(db, staff_id, current_user.id)
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


@router.post("/staff/{staff_id}/design-pricing", response_model=DesignPricingPermissionResponse)
def set_design_pricing_permission_endpoint(
    staff_id: UUID,
    payload: DesignPricingPermissionRequest,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> DesignPricingPermissionResponse:
    try:
        staff = set_design_pricing_permission(db, staff_id, payload.enabled)
    except StaffNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")

    return DesignPricingPermissionResponse(
        staff_id=staff.id, can_price_custom_designs=staff.can_price_custom_designs
    )


@router.post("/staff/{staff_id}/slot-lock-permission", response_model=SlotLockPermissionResponse)
def set_slot_lock_permission_endpoint(
    staff_id: UUID,
    payload: SlotLockPermissionRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.ADMIN)),
) -> SlotLockPermissionResponse:
    staff = db.get(StaffModel, staff_id)
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")

    staff.can_lock_slots = payload.enabled
    db.add(
        AuditLogModel(
            actor_user_id=current_user.id,
            action="staff.slot_lock_permission",
            entity_type="staff",
            entity_id=staff.id,
            details={"enabled": payload.enabled},
        )
    )
    db.commit()
    db.refresh(staff)

    return SlotLockPermissionResponse(staff_id=staff.id, can_lock_slots=staff.can_lock_slots)


@router.patch("/users/{user_id}", response_model=UserAdminResponse)
def update_user_endpoint(
    user_id: UUID,
    payload: UserUpdateRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles(UserRole.ADMIN)),
) -> UserAdminResponse:
    user = db.get(UserModel, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own role or status",
        )

    changes = {}
    if payload.role is not None and payload.role != user.role:
        changes["role"] = {"from": user.role.value, "to": payload.role.value}
        user.role = payload.role
    if payload.status is not None and payload.status != user.status:
        changes["status"] = {"from": user.status.value, "to": payload.status.value}
        user.status = payload.status

    if changes:
        db.add(
            AuditLogModel(
                actor_user_id=current_user.id,
                action="user.updated",
                entity_type="user",
                entity_id=user.id,
                details=changes,
            )
        )
    db.commit()
    db.refresh(user)

    return UserAdminResponse(
        id=user.id,
        phone_number=user.phone_number,
        email=user.email,
        role=user.role.value,
        status=user.status.value,
    )
