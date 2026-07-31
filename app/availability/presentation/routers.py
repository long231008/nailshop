from datetime import date as date_type
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.availability.application.slot_finder import (
    BookingWindowClosedError,
    ServiceNotFoundError,
    find_available_slots,
)
from app.availability.presentation.schemas import AvailabilityResponse, AvailableSlot
from app.shared.infrastructure.database.session import get_db

router = APIRouter(prefix="/availability", tags=["availability"])


@router.get("", response_model=AvailabilityResponse)
def get_availability(
    branch_id: UUID,
    date: date_type,
    service_id: UUID | None = None,
    service_ids: str | None = Query(
        default=None,
        description="Comma-separated service ids of the whole visit, in order",
    ),
    staff_id: UUID | None = None,
    db: Session = Depends(get_db),
) -> AvailabilityResponse:
    ids: list[UUID] = []
    if service_ids:
        try:
            ids = [UUID(part) for part in service_ids.split(",") if part.strip()]
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="service_ids must be comma-separated UUIDs",
            )
    elif service_id is not None:
        ids = [service_id]
    if not ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide service_id or service_ids",
        )

    try:
        slots = find_available_slots(db, branch_id, ids, date, staff_id)
    except ServiceNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    except BookingWindowClosedError as exc:
        return AvailabilityResponse(window=exc.state, slots=[])

    return AvailabilityResponse(window="open", slots=[AvailableSlot(**slot) for slot in slots])
