from datetime import date

from app.bookings.infrastructure.models import BookingModel, BookingStatus


def test_dashboard_summary_counts_and_revenue(
    client, admin_headers, seeded_branch, seeded_staff, cleanup_records, db_session,
    customer_identity,
):
    booking = BookingModel(
        customer_id=customer_identity["id"],
        branch_id=seeded_branch,
        booking_date=date.today(),
        status=BookingStatus.COMPLETED,
        total_price=30.0,
        final_price=33.0,
    )
    db_session.add(booking)
    db_session.commit()
    cleanup_records.append(("bookings", booking.id))

    ticket = client.post("/app/queue/scan", json={"branch_id": str(seeded_branch)}).json()
    cleanup_records.append(("queue_tickets", ticket["id"]))

    response = client.get(
        "/app/dashboard/summary", params={"branch_id": str(seeded_branch)}, headers=admin_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["bookings_today"]["completed"] == 1
    assert body["revenue_today"] == 33.0
    assert body["queue_waiting_count"] == 1
    assert body["active_staff_count"] == 1


def test_dashboard_summary_requires_admin(client, customer_headers):
    response = client.get("/app/dashboard/summary", headers=customer_headers)
    assert response.status_code == 403
