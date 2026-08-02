import uuid

from app.bookings.infrastructure.models import BookingModel, BookingStatus
from app.shared.infrastructure.clock import today_in_shop_tz
from app.webhooks.infrastructure.models import (
    PaymentTransactionModel,
    PaymentTransactionStatus,
    PaymentTransactionType,
)


def _create_booking(db_session, cleanup_records, customer_id, branch_id):
    booking = BookingModel(
        customer_id=customer_id,
        branch_id=branch_id,
        # The dashboard's "today" follows the shop's wall clock, not UTC.
        booking_date=today_in_shop_tz(),
        status=BookingStatus.COMPLETED,
        total_price=30.0,
        final_price=33.0,
    )
    db_session.add(booking)
    db_session.commit()
    cleanup_records.append(("bookings", booking.id))
    return booking


def _create_transaction(db_session, cleanup_records, booking_id, amount, transaction_type, status):
    transaction = PaymentTransactionModel(
        booking_id=booking_id,
        provider_transaction_id=str(uuid.uuid4()),
        amount=amount,
        transaction_type=transaction_type,
        status=status,
    )
    db_session.add(transaction)
    db_session.commit()
    cleanup_records.append(("payment_transactions", transaction.id))
    return transaction


def test_dashboard_summary_counts_bookings_and_queue(
    client,
    admin_headers,
    seeded_branch,
    seeded_staff,
    cleanup_records,
    db_session,
    customer_identity,
):
    _create_booking(db_session, cleanup_records, customer_identity["id"], seeded_branch)

    ticket = client.post("/app/queue/scan", json={"branch_id": str(seeded_branch)}).json()
    cleanup_records.append(("queue_tickets", ticket["id"]))

    response = client.get(
        "/app/dashboard/summary", params={"branch_id": str(seeded_branch)}, headers=admin_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["bookings_today"]["completed"] == 1
    assert body["queue_waiting_count"] == 1
    assert body["active_staff_count"] == 1
    assert "revenue_today" not in body


def test_dashboard_summary_requires_admin(client, customer_headers):
    response = client.get("/app/dashboard/summary", headers=customer_headers)
    assert response.status_code == 403


def test_dashboard_revenue_separates_deposit_and_total(
    client,
    admin_headers,
    seeded_branch,
    cleanup_records,
    db_session,
    customer_identity,
):
    booking = _create_booking(db_session, cleanup_records, customer_identity["id"], seeded_branch)
    _create_transaction(
        db_session,
        cleanup_records,
        booking.id,
        30.0,
        PaymentTransactionType.DEPOSIT,
        PaymentTransactionStatus.SUCCESS,
    )
    _create_transaction(
        db_session,
        cleanup_records,
        booking.id,
        70.0,
        PaymentTransactionType.FINAL_PAYMENT,
        PaymentTransactionStatus.SUCCESS,
    )

    response = client.get(
        "/app/dashboard/revenue", params={"branch_id": str(seeded_branch)}, headers=admin_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deposit_revenue"]["today"] == 30.0
    assert body["deposit_revenue"]["this_year"] == 30.0
    assert body["total_revenue"]["today"] == 100.0
    assert body["total_revenue"]["this_year"] == 100.0


def test_dashboard_revenue_ignores_pending_and_failed_transactions(
    client,
    admin_headers,
    seeded_branch,
    cleanup_records,
    db_session,
    customer_identity,
):
    booking = _create_booking(db_session, cleanup_records, customer_identity["id"], seeded_branch)
    _create_transaction(
        db_session,
        cleanup_records,
        booking.id,
        999.0,
        PaymentTransactionType.DEPOSIT,
        PaymentTransactionStatus.PENDING,
    )
    _create_transaction(
        db_session,
        cleanup_records,
        booking.id,
        999.0,
        PaymentTransactionType.DEPOSIT,
        PaymentTransactionStatus.FAILED,
    )

    response = client.get(
        "/app/dashboard/revenue", params={"branch_id": str(seeded_branch)}, headers=admin_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deposit_revenue"]["today"] == 0.0
    assert body["total_revenue"]["today"] == 0.0


def test_dashboard_revenue_scoped_to_branch(
    client,
    admin_headers,
    seeded_branch,
    cleanup_records,
    db_session,
    customer_identity,
):
    other_branch = client.post(
        "/app/branches",
        json={"name": f"Other-{uuid.uuid4().hex[:6]}", "address": "x"},
        headers=admin_headers,
    ).json()
    cleanup_records.append(("locations", other_branch["id"]))

    other_booking = _create_booking(
        db_session, cleanup_records, customer_identity["id"], other_branch["id"]
    )
    _create_transaction(
        db_session,
        cleanup_records,
        other_booking.id,
        500.0,
        PaymentTransactionType.DEPOSIT,
        PaymentTransactionStatus.SUCCESS,
    )

    response = client.get(
        "/app/dashboard/revenue", params={"branch_id": str(seeded_branch)}, headers=admin_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deposit_revenue"]["today"] == 0.0


def test_dashboard_revenue_requires_admin(client, customer_headers):
    response = client.get("/app/dashboard/revenue", headers=customer_headers)
    assert response.status_code == 403


def test_revenue_subtracts_refunds(
    client,
    admin_headers,
    seeded_branch,
    cleanup_records,
    db_session,
    customer_identity,
):
    booking = _create_booking(db_session, cleanup_records, customer_identity["id"], seeded_branch)
    _create_transaction(
        db_session,
        cleanup_records,
        booking.id,
        9.0,
        PaymentTransactionType.DEPOSIT,
        PaymentTransactionStatus.SUCCESS,
    )
    _create_transaction(
        db_session,
        cleanup_records,
        booking.id,
        24.0,
        PaymentTransactionType.FINAL_PAYMENT,
        PaymentTransactionStatus.SUCCESS,
    )
    _create_transaction(
        db_session,
        cleanup_records,
        booking.id,
        9.0,
        PaymentTransactionType.REFUND,
        PaymentTransactionStatus.SUCCESS,
    )

    response = client.get(
        "/app/dashboard/revenue", params={"branch_id": str(seeded_branch)}, headers=admin_headers
    )

    assert response.status_code == 200
    data = response.json()
    # Deposits taken stay visible, but refunded money is not revenue.
    assert data["deposit_revenue"]["today"] == 9.0
    assert data["total_revenue"]["today"] == 24.0
