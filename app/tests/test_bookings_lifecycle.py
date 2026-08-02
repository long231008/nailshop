import uuid as uuid_lib


def _create_booking(
    client,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": seeded_shift["start"].isoformat(),
            }
        ],
    }
    response = client.post("/app/bookings", json=payload, headers=customer_headers)
    assert response.status_code == 201
    body = response.json()
    cleanup_records.append(("bookings", body["id"]))
    cleanup_records.append(("booking_details", body["details"][0]["id"]))
    return body


def test_approve_booking_creates_soft_lock_and_deposit(
    client,
    admin_headers,
    customer_headers,
    fake_redis,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    booking = _create_booking(
        client,
        customer_headers,
        seeded_branch,
        seeded_service,
        seeded_staff,
        seeded_shift,
        cleanup_records,
    )

    response = client.post(f"/app/bookings/{booking['id']}/approve", headers=admin_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["deposit_amount"] == 6.0
    assert body["soft_lock_expires_in_seconds"] == 900

    lock_key = f"booking:soft_lock:{booking['id']}"
    assert fake_redis.get(lock_key) == "awaiting_deposit"
    assert 0 < fake_redis.ttl(lock_key) <= 900


def test_cannot_approve_twice(
    client,
    admin_headers,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    booking = _create_booking(
        client,
        customer_headers,
        seeded_branch,
        seeded_service,
        seeded_staff,
        seeded_shift,
        cleanup_records,
    )
    client.post(f"/app/bookings/{booking['id']}/approve", headers=admin_headers)

    response = client.post(f"/app/bookings/{booking['id']}/approve", headers=admin_headers)

    assert response.status_code == 400


def test_customer_can_view_own_status_but_not_others(
    client,
    customer_headers,
    other_customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
):
    booking = _create_booking(
        client,
        customer_headers,
        seeded_branch,
        seeded_service,
        seeded_staff,
        seeded_shift,
        cleanup_records,
    )

    own = client.get(f"/app/bookings/{booking['id']}/status", headers=customer_headers)
    assert own.status_code == 200

    other = client.get(f"/app/bookings/{booking['id']}/status", headers=other_customer_headers)
    assert other.status_code == 403


def test_start_service_and_complete_flow(
    client,
    admin_headers,
    customer_headers,
    seeded_staff,
    seeded_branch,
    seeded_service,
    seeded_shift,
    cleanup_records,
    db_session,
):
    booking = _create_booking(
        client,
        customer_headers,
        seeded_branch,
        seeded_service,
        seeded_staff,
        seeded_shift,
        cleanup_records,
    )
    client.post(f"/app/bookings/{booking['id']}/approve", headers=admin_headers)
    detail_id = booking["details"][0]["id"]

    from app.allocation.application.materialize import materialize_day
    from app.shared.infrastructure.clock import shop_timezone

    day = seeded_shift["start"].astimezone(shop_timezone()).date()
    run = materialize_day(db_session, seeded_branch, day)
    cleanup_records.append(("allocation_runs", run.id))

    start_response = client.post(
        f"/app/staff/{seeded_staff['staff_id']}/start-service",
        json={"booking_detail_id": detail_id},
        headers=seeded_staff["headers"],
    )
    assert start_response.status_code == 200
    assert start_response.json()["status"] == "in_progress"

    complete_response = client.post(
        f"/app/bookings/{booking['id']}/complete",
        json={"booking_detail_id": detail_id},
        headers=seeded_staff["headers"],
    )
    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "completed"


def test_staff_cannot_start_service_while_busy_with_another(
    client,
    admin_headers,
    customer_headers,
    seeded_staff,
    seeded_branch,
    seeded_service,
    seeded_shift,
    cleanup_records,
    db_session,
):
    first = _create_booking(
        client,
        customer_headers,
        seeded_branch,
        seeded_service,
        seeded_staff,
        seeded_shift,
        cleanup_records,
    )
    client.post(f"/app/bookings/{first['id']}/approve", headers=admin_headers)

    second_payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": (seeded_shift["start"].replace(hour=13)).isoformat(),
            }
        ],
    }
    second = client.post("/app/bookings", json=second_payload, headers=customer_headers)
    cleanup_records.append(("bookings", second.json()["id"]))
    cleanup_records.append(("booking_details", second.json()["details"][0]["id"]))

    from app.allocation.application.materialize import materialize_day
    from app.shared.infrastructure.clock import shop_timezone

    day = seeded_shift["start"].astimezone(shop_timezone()).date()
    run = materialize_day(db_session, seeded_branch, day)
    cleanup_records.append(("allocation_runs", run.id))

    client.post(
        f"/app/staff/{seeded_staff['staff_id']}/start-service",
        json={"booking_detail_id": first["details"][0]["id"]},
        headers=seeded_staff["headers"],
    )

    response = client.post(
        f"/app/staff/{seeded_staff['staff_id']}/start-service",
        json={"booking_detail_id": second.json()["details"][0]["id"]},
        headers=seeded_staff["headers"],
    )

    assert response.status_code == 409


def test_no_show_marks_booking_and_writes_audit_log(
    client,
    admin_headers,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
    db_session,
):
    booking = _create_booking(
        client,
        customer_headers,
        seeded_branch,
        seeded_service,
        seeded_staff,
        seeded_shift,
        cleanup_records,
    )
    client.post(f"/app/bookings/{booking['id']}/approve", headers=admin_headers)

    response = client.post(f"/app/bookings/{booking['id']}/no-show", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "no_show"

    audit_response = client.get("/app/audit-log", headers=admin_headers)
    actions = [entry["action"] for entry in audit_response.json()]
    assert "booking.no_show" in actions

    from app.audit_log.infrastructure.models import AuditLogModel

    log_entry = (
        db_session.query(AuditLogModel)
        .filter(AuditLogModel.entity_id == uuid_lib.UUID(booking["id"]))
        .first()
    )
    cleanup_records.append(("audit_logs", log_entry.id))


def test_complete_manual_final_price_and_bill(
    client,
    admin_headers,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
    db_session,
):
    booking = _create_booking(
        client,
        customer_headers,
        seeded_branch,
        seeded_service,
        seeded_staff,
        seeded_shift,
        cleanup_records,
    )
    client.post(f"/app/bookings/{booking['id']}/approve", headers=admin_headers)

    complete_response = client.post(
        f"/app/bookings/{booking['id']}/complete-manual", headers=admin_headers
    )
    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "completed"

    from app.audit_log.infrastructure.models import AuditLogModel

    log_entry = (
        db_session.query(AuditLogModel)
        .filter(AuditLogModel.entity_id == uuid_lib.UUID(booking["id"]))
        .first()
    )
    cleanup_records.append(("audit_logs", log_entry.id))

    final_price_response = client.post(
        f"/app/bookings/{booking['id']}/final-price",
        json={"final_price": 25.0},
        headers=admin_headers,
    )
    assert final_price_response.status_code == 200
    assert final_price_response.json()["final_price"] == 25.0

    bill_response = client.get(f"/app/bookings/{booking['id']}/bill", headers=admin_headers)
    assert bill_response.status_code == 200
    bill = bill_response.json()
    assert bill["total"] == 25.0
    assert bill["deposit"] == 6.0
    assert bill["remaining"] == 19.0


def test_cancel_after_paid_deposit_flags_refund_in_audit(
    client,
    admin_headers,
    customer_headers,
    seeded_branch,
    seeded_service,
    seeded_staff,
    seeded_shift,
    cleanup_records,
    db_session,
):
    import hashlib
    import hmac
    import json

    from app.shared.infrastructure.config.settings import settings

    booking = _create_booking(
        client,
        customer_headers,
        seeded_branch,
        seeded_service,
        seeded_staff,
        seeded_shift,
        cleanup_records,
    )
    approve = client.post(f"/app/bookings/{booking['id']}/approve", headers=admin_headers).json()

    body = json.dumps(
        {
            "transaction_id": str(uuid_lib.uuid4()),
            "booking_id": booking["id"],
            "amount": approve["deposit_amount"],
            "transaction_type": "deposit",
        }
    ).encode()
    signature = hmac.new(settings.PAYMENT_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    paid = client.post(
        "/app/webhooks/payment",
        content=body,
        headers={"X-Signature": signature, "Content-Type": "application/json"},
    )
    assert paid.status_code == 200

    response = client.post(f"/app/bookings/{booking['id']}/cancel", headers=customer_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    from app.audit_log.infrastructure.models import AuditLogModel
    from app.webhooks.infrastructure.models import PaymentTransactionModel

    transaction = (
        db_session.query(PaymentTransactionModel)
        .filter(PaymentTransactionModel.booking_id == uuid_lib.UUID(booking["id"]))
        .first()
    )
    cleanup_records.append(("payment_transactions", transaction.id))

    log_entry = (
        db_session.query(AuditLogModel)
        .filter(
            AuditLogModel.entity_id == uuid_lib.UUID(booking["id"]),
            AuditLogModel.action == "booking.cancelled_by_customer",
        )
        .first()
    )
    cleanup_records.append(("audit_logs", log_entry.id))
    # No automatic refund exists: the audit entry tells the salon money is owed.
    assert log_entry.details["deposit_paid"] is True
    assert log_entry.details["deposit_amount"] == approve["deposit_amount"]
