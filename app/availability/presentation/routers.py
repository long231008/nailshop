from datetime import date as date_type
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.availability.application.slot_finder import ServiceNotFoundError, find_available_slots
from app.availability.presentation.schemas import AvailableSlot
from app.shared.infrastructure.database.session import get_db

router = APIRouter(prefix="/availability", tags=["availability"])


@router.get("", response_model=list[AvailableSlot])
def get_availability(
    branch_id: UUID,
    service_id: UUID,
    date: date_type,
    staff_id: UUID | None = None,
    service_extension_id: UUID | None = None,
    duration_min: int | None = Query(
        default=None,
        ge=15,
        le=480,
        description="Override the slot duration, e.g. the combined length of a cart",
    ),
    db: Session = Depends(get_db),
) -> list[AvailableSlot]:
    try:
        slots = find_available_slots(
            db, branch_id, service_id, date, staff_id, service_extension_id, duration_min
        )
    except ServiceNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")

    return [AvailableSlot(**slot) for slot in slots]
