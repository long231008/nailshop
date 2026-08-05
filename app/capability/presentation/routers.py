from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.domain.value_object import UserRole
from app.branches.infrastructure.models import LocationModel
from app.capability.application.matrix import (
    CapabilityValidationError,
    get_matrix_payload,
    save_matrix,
)
from app.capability.presentation.schemas import (
    MatrixResponse,
    MatrixSaveRequest,
    MatrixSaveResponse,
    MatrixServiceItem,
    MatrixStaffItem,
)
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.database.session import get_db
from app.shared.presentation.dependencies import require_roles
from app.staff.infrastructure.models import StaffModel

router = APIRouter(prefix="/capability", tags=["capability"])


def _matrix_response(db: Session) -> MatrixResponse:
    payload = get_matrix_payload(db)
    return MatrixResponse(
        services=[
            MatrixServiceItem(
                id=s.id,
                branch_id=s.branch_id,
                name=s.name,
                category=s.category,
                description=s.description,
                duration_min=s.duration_min,
                base_price=float(s.base_price),
                skill_group=s.skill_group,
                body_zone=s.body_zone,
                resource=s.resource,
                turn_weight=float(s.turn_weight),
                buffer_after_min=s.buffer_after_min,
            )
            for s in payload["services"]
        ],
        staff=[
            MatrixStaffItem(
                id=t.id,
                branch_id=t.branch_id,
                display_name=t.display_name,
                status=t.status.value,
                days_off=t.days_off,
                work_start_hour=t.work_start_hour,
                work_end_hour=t.work_end_hour,
                max_hours_week=t.max_hours_week,
            )
            for t in payload["staff"]
        ],
        capability=payload["capability"],
    )


@router.get("/matrix", response_model=MatrixResponse)
def get_matrix(
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> MatrixResponse:
    return _matrix_response(db)


@router.put("/matrix", response_model=MatrixSaveResponse)
def put_matrix(
    payload: MatrixSaveRequest,
    db: Session = Depends(get_db),
    _=Depends(require_roles(UserRole.ADMIN)),
) -> MatrixSaveResponse:
    # 1. Upsert the price list. Services absent from the payload are left alone -
    #    old bookings keep referencing them.
    known_service_ids = set()
    for item in payload.services:
        if item.branch_id is not None and db.get(LocationModel, item.branch_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")
        if item.id is not None:
            service = db.get(ServiceModel, item.id)
            if service is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Service not found"
                )
            for field in (
                "branch_id",
                "name",
                "category",
                "description",
                "duration_min",
                "base_price",
                "skill_group",
                "body_zone",
                "resource",
                "turn_weight",
                "buffer_after_min",
            ):
                setattr(service, field, getattr(item, field))
        else:
            service = ServiceModel(**item.model_dump(exclude={"id"}))
            db.add(service)
        db.flush()
        known_service_ids.add(service.id)

    # 2. Per-tech scheduling settings (days off feed the capacity ledger).
    for staff_item in payload.staff:
        staff = db.get(StaffModel, staff_item.id)
        if staff is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
        staff.days_off = staff_item.days_off
        staff.work_start_hour = staff_item.work_start_hour
        staff.work_end_hour = staff_item.work_end_hour
        staff.max_hours_week = staff_item.max_hours_week

    # 3. Replace the capability matrix (runs the on_capability_change checks).
    try:
        warnings, affected = save_matrix(db, payload.capability)
    except CapabilityValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.detail)

    return MatrixSaveResponse(warnings=warnings, affected_bookings=affected)
