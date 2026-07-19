def test_reserve_staff(client, admin_headers, seeded_staff):
    response = client.post(
        f"/app/admin/staff/{seeded_staff['staff_id']}/reserve", headers=admin_headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "reserved"


def test_block_staff_deletes_future_shifts_and_unassigns_future_bookings(
    client, admin_headers, customer_headers, seeded_branch, seeded_service, seeded_staff,
    seeded_shift, cleanup_records, db_session,
):
    booking_payload = {
        "branch_id": str(seeded_branch),
        "items": [
            {
                "service_id": str(seeded_service),
                "staff_id": str(seeded_staff["staff_id"]),
                "start_time": seeded_shift["start"].isoformat(),
            }
        ],
    }
    booking = client.post("/app/bookings", json=booking_payload, headers=customer_headers).json()
    cleanup_records.append(("bookings", booking["id"]))
    cleanup_records.append(("booking_details", booking["details"][0]["id"]))

    response = client.post(
        f"/app/admin/staff/{seeded_staff['staff_id']}/block", headers=admin_headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert response.json()["reassigned_bookings"] == 1

    from app.bookings.infrastructure.models import BookingDetailModel
    from app.shifts.infrastructure.models import StaffRosterModel

    detail = db_session.get(BookingDetailModel, booking["details"][0]["id"])
    db_session.refresh(detail)
    assert detail.staff_id is None

    shift_count = (
        db_session.query(StaffRosterModel)
        .filter(StaffRosterModel.staff_id == seeded_staff["staff_id"])
        .count()
    )
    assert shift_count == 0

    from app.audit_log.infrastructure.models import AuditLogModel

    log_entry = (
        db_session.query(AuditLogModel)
        .filter(
            AuditLogModel.entity_id == seeded_staff["staff_id"],
            AuditLogModel.action == "staff.block",
        )
        .first()
    )
    cleanup_records.append(("audit_logs", log_entry.id))
