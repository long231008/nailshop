from datetime import date as date_type
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.infrastructure.models import UserModel
from app.bookings.infrastructure.models import BookingDetailModel, BookingModel, BookingStatus
from app.custom_designs.infrastructure.models import CustomDesignModel
from app.services.infrastructure.models import ServiceModel
from app.shared.infrastructure.clock import day_bounds_utc, now_utc
from app.staff.infrastructure.models import StaffModel
from app.webhooks.infrastructure.models import (
    PaymentTransactionModel,
    PaymentTransactionStatus,
    PaymentTransactionType,
)

VISIBLE_STATUSES = [
    BookingStatus.PENDING,
    BookingStatus.APPROVED,
    BookingStatus.IN_PROGRESS,
    BookingStatus.COMPLETED,
]


def _paid_deposit_booking_ids(db: Session, booking_ids: list[UUID]) -> set[UUID]:
    if not booking_ids:
        return set()
    rows = (
        db.query(PaymentTransactionModel.booking_id)
        .filter(
            PaymentTransactionModel.booking_id.in_(booking_ids),
            PaymentTransactionModel.transaction_type == PaymentTransactionType.DEPOSIT,
            PaymentTransactionModel.status == PaymentTransactionStatus.SUCCESS,
        )
        .all()
    )
    return {row[0] for row in rows}


def get_daily_schedule(
    db: Session, target_date: date_type, branch_id: UUID | None
) -> tuple[list[dict], list[dict]]:
    """(appointments, pending) for one local day.

    A booking earns its place on the day grid once it is granted AND the deposit
    has arrived (or the customer is already in the chair / finished). Everything
    else that still needs a human - waiting for approval, waiting for its
    deposit - goes to the pending list.
    """
    day_start, day_end = day_bounds_utc(target_date)

    query = (
        db.query(
            BookingDetailModel,
            BookingModel,
            ServiceModel.name,
            StaffModel,
            UserModel,
            CustomDesignModel,
        )
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .join(ServiceModel, BookingDetailModel.service_id == ServiceModel.id)
        .outerjoin(StaffModel, BookingDetailModel.staff_id == StaffModel.id)
        .join(UserModel, BookingModel.customer_id == UserModel.id)
        .outerjoin(CustomDesignModel, BookingDetailModel.custom_design_id == CustomDesignModel.id)
        .filter(
            BookingModel.status.in_(VISIBLE_STATUSES),
            BookingDetailModel.start_time >= day_start,
            BookingDetailModel.start_time < day_end,
        )
    )
    if branch_id is not None:
        query = query.filter(BookingModel.branch_id == branch_id)

    rows = query.order_by(BookingDetailModel.start_time).all()
    paid_ids = _paid_deposit_booking_ids(db, list({booking.id for _, booking, *_ in rows}))

    appointments: list[dict] = []
    pending: list[dict] = []
    for detail, booking, service_name, staff, customer, design in rows:
        customer_name = " ".join(part for part in (customer.first_name, customer.surname) if part)
        item = {
            "booking_id": booking.id,
            "branch_id": booking.branch_id,
            "start_time": detail.start_time,
            "end_time": detail.end_time,
            "service_name": service_name,
            "staff_name": staff.display_name if staff else None,
            "customer_name": customer_name or None,
            "customer_phone": customer.phone_number,
            "price": float(detail.price),
            "status": booking.status.value,
            # The customer's nail art, so staff can see what they'll be doing.
            "design_image_url": design.image_url if design else None,
            "design_description": design.description if design else None,
        }

        if (
            booking.status in (BookingStatus.IN_PROGRESS, BookingStatus.COMPLETED)
            or (booking.status == BookingStatus.APPROVED and booking.id in paid_ids)
            # A booking the desk entered itself is already agreed in person, so it
            # sits on the grid without waiting for an online deposit.
            or (booking.status == BookingStatus.APPROVED and booking.staff_created)
        ):
            appointments.append(item)
        elif booking.status == BookingStatus.APPROVED:
            pending.append({**item, "stage": "awaiting_deposit"})
        else:
            pending.append({**item, "stage": "awaiting_approval"})

    return appointments, pending


def get_upcoming_pending(db: Session, branch_id: UUID | None) -> list[dict]:
    """Every future booking still needing a human, however far ahead it is.

    A request made months out must not hide behind day-by-day navigation, so
    this list spans all dates: bookings awaiting a grant, and granted ones whose
    deposit has not arrived yet.
    """
    query = (
        db.query(
            BookingDetailModel,
            BookingModel,
            ServiceModel.name,
            StaffModel,
            UserModel,
            CustomDesignModel,
        )
        .join(BookingModel, BookingDetailModel.booking_id == BookingModel.id)
        .join(ServiceModel, BookingDetailModel.service_id == ServiceModel.id)
        .outerjoin(StaffModel, BookingDetailModel.staff_id == StaffModel.id)
        .join(UserModel, BookingModel.customer_id == UserModel.id)
        .outerjoin(CustomDesignModel, BookingDetailModel.custom_design_id == CustomDesignModel.id)
        .filter(
            BookingModel.status.in_([BookingStatus.PENDING, BookingStatus.APPROVED]),
            BookingDetailModel.start_time >= now_utc(),
        )
    )
    if branch_id is not None:
        query = query.filter(BookingModel.branch_id == branch_id)

    rows = query.order_by(BookingDetailModel.start_time).all()
    paid_ids = _paid_deposit_booking_ids(db, list({booking.id for _, booking, *_ in rows}))

    pending: list[dict] = []
    for detail, booking, service_name, staff, customer, design in rows:
        if booking.status == BookingStatus.APPROVED and (
            booking.id in paid_ids or booking.staff_created
        ):
            continue  # secured (deposit paid or desk-entered) - it lives on the grid
        customer_name = " ".join(part for part in (customer.first_name, customer.surname) if part)
        pending.append(
            {
                "booking_id": booking.id,
                "branch_id": booking.branch_id,
                "start_time": detail.start_time,
                "end_time": detail.end_time,
                "service_name": service_name,
                "staff_name": staff.display_name if staff else None,
                "customer_name": customer_name or None,
                "customer_phone": customer.phone_number,
                "price": float(detail.price),
                "status": booking.status.value,
                "design_image_url": design.image_url if design else None,
                "design_description": design.description if design else None,
                "stage": (
                    "awaiting_deposit"
                    if booking.status == BookingStatus.APPROVED
                    else "awaiting_approval"
                ),
            }
        )
    return pending
