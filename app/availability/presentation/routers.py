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
    extension_ids: str | None = Query(
        default=None,
        description=(
            "Comma-separated length ids, positionally matching service_ids; "
            "leave an entry empty for a service booked at its standard length"
        ),
    ),
    staff_id: UUID | None = Query(
        default=None,
        description=(
            "Accepted for compatibility but ignored: a preferred technician "
            "does not change which times are sellable"
        ),
    ),
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

    lengths: list[UUID | None] | None = None
    if extension_ids:
        try:
            lengths = [
                UUID(part.strip()) if part.strip() else None for part in extension_ids.split(",")
            ]
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="extension_ids must be comma-separated UUIDs",
            )
        if len(lengths) != len(ids):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="extension_ids must have one entry per service",
            )

    try:
        slots = find_available_slots(db, branch_id, ids, date, lengths)
    except ServiceNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    except BookingWindowClosedError as exc:
        return AvailabilityResponse(window=exc.state, slots=[])

    return AvailabilityResponse(window="open", slots=[AvailableSlot(**slot) for slot in slots])
