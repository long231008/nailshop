"""Remove leftovers the automated tests and the simulator wrote into this
database.

Test rows carry unmistakable signatures and real data carries none of them:

- test/sim accounts: 10-digit phone numbers starting 09 (the suite invents
  "09xxxxxxxx", the simulator "0999..."), or an @example.com email. Real
  customers type UK numbers (07...), and the seeded demo salon uses 07 too.
- test branches: names like "Branch-a1b2c3" / "Other-..." / "Pin-...", or the
  fixture addresses "1 Test St" / "2 Test St".
- test services: attached to a test branch, or hex-suffixed names like
  "Pedicure-8ce217", or the fixed names the suite invents.

Everything referencing those rows (bookings, payments, designs, capability
cells, locks, rosters, audit trails) goes with them, in foreign-key order.
Anything ambiguous is skipped and reported, never guessed at.

Dry run by default - prints what would go and changes nothing:

    python scripts/purge_test_data.py

Apply for real:

    python scripts/purge_test_data.py --apply
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.shared.infrastructure.database.session import SessionLocal  # noqa: E402

TEST_BRANCH_NAME = r"^(Branch|Res|Other|Pin|Dedupe|DBG|CLI)-[0-9a-f]{6}$"
TEST_SERVICE_NAME = r"^(Pedicure|Service|Long|Widening|Branch)-[0-9a-f]{6}$"
# Fixed names only the test-suite ever invents. Deliberately NOT including
# plausible real menu items like "Gel Manicure" or "Nail Art".
TEST_SERVICE_FIXED = [
    "Widening",
    "Slow Only",
    "Elsewhere",
    "No Budget",
    "Doubled Gel",
    "Chair Pedi",
    "CLI Gel Set",
]


def _ids(db, sql, **params):
    return [str(row[0]) for row in db.execute(text(sql), params)]


def purge(db, apply: bool = False, keep: set[str] | None = None) -> list[str]:
    """keep: phone numbers/emails that must never be treated as test accounts
    (for staff genuinely registered with an 09... number)."""
    report: list[str] = []
    keep = keep or set()

    user_sql = (
        "SELECT id FROM users WHERE (phone_number ~ '^09[0-9]{8}$' "
        "OR email LIKE '%@example.com')"
    )
    if keep:
        user_sql += (
            " AND COALESCE(phone_number, '') != ALL(:keep)"
            " AND COALESCE(email, '') != ALL(:keep)"
        )
    users = _ids(db, user_sql, **({"keep": sorted(keep)} if keep else {}))
    samples = db.execute(
        text(
            "SELECT COALESCE(phone_number, email) FROM users "
            "WHERE id = ANY(CAST(:ids AS uuid[])) LIMIT 15"
        ),
        {"ids": users},
    ).scalars().all()
    report.append(f"test accounts: {len(users)}" + (f" (e.g. {', '.join(samples)})" if samples else ""))

    # Staff are somebody's livelihood on this screen - name every one that
    # would go, so a real technician registered with an 09... number is
    # spotted before anything is applied (then re-run with --keep <phone>).
    doomed_staff = db.execute(
        text(
            "SELECT s.display_name, COALESCE(u.phone_number, u.email) FROM staff s "
            "JOIN users u ON u.id = s.user_id WHERE s.user_id = ANY(CAST(:u AS uuid[]))"
        ),
        {"u": users},
    ).all()
    for name, contact in doomed_staff:
        report.append(f"  staff to delete: {name} ({contact})")

    branches = _ids(
        db,
        "SELECT id FROM locations WHERE name ~ :pattern "
        "OR name LIKE 'Elsewhere %' OR address IN ('1 Test St', '2 Test St')",
        pattern=TEST_BRANCH_NAME,
    )
    services = _ids(
        db,
        "SELECT id FROM services WHERE branch_id = ANY(CAST(:branches AS uuid[])) "
        "OR name ~ :pattern OR name = ANY(:fixed) "
        "OR name LIKE 'Dedupe %' OR name LIKE 'DBG %'",
        branches=branches,
        pattern=TEST_SERVICE_NAME,
        fixed=TEST_SERVICE_FIXED,
    )
    staff = _ids(db, "SELECT id FROM staff WHERE user_id = ANY(CAST(:u AS uuid[]))", u=users)
    bookings = _ids(
        db,
        "SELECT id FROM bookings WHERE customer_id = ANY(CAST(:u AS uuid[])) "
        "OR branch_id = ANY(CAST(:b AS uuid[])) "
        "OR id IN (SELECT booking_id FROM booking_details "
        "          WHERE service_id = ANY(CAST(:s AS uuid[])))",
        u=users,
        b=branches,
        s=services,
    )
    report.append(
        f"test branches: {len(branches)}, test services: {len(services)}, "
        f"test staff: {len(staff)}, test bookings: {len(bookings)}"
    )

    everything = users + branches + services + staff + bookings

    def run(label, sql, **params):
        count = db.execute(text(sql), params).rowcount
        if count:
            report.append(f"  {label}: {count}")

    run(
        "audit log entries",
        "DELETE FROM audit_logs WHERE actor_user_id = ANY(CAST(:u AS uuid[])) "
        "OR entity_id = ANY(CAST(:all AS uuid[]))",
        u=users,
        all=everything,
    )
    run(
        "payment transactions",
        "DELETE FROM payment_transactions WHERE booking_id = ANY(CAST(:b AS uuid[]))",
        b=bookings,
    )
    run(
        "booking legs",
        "DELETE FROM booking_details WHERE booking_id = ANY(CAST(:b AS uuid[]))",
        b=bookings,
    )
    run(
        "custom designs",
        "DELETE FROM custom_designs WHERE customer_id = ANY(CAST(:u AS uuid[]))",
        u=users,
    )
    run("bookings", "DELETE FROM bookings WHERE id = ANY(CAST(:b AS uuid[]))", b=bookings)
    run(
        "slot locks",
        "DELETE FROM slot_locks WHERE branch_id = ANY(CAST(:b AS uuid[])) "
        "OR staff_id = ANY(CAST(:s AS uuid[])) OR created_by = ANY(CAST(:u AS uuid[]))",
        b=branches,
        s=staff,
        u=users,
    )
    run(
        "leave windows",
        "DELETE FROM staff_leaves WHERE staff_id = ANY(CAST(:s AS uuid[]))",
        s=staff,
    )
    run(
        "day assignments",
        "DELETE FROM staff_day_assignments WHERE staff_id = ANY(CAST(:s AS uuid[])) "
        "OR branch_id = ANY(CAST(:b AS uuid[]))",
        s=staff,
        b=branches,
    )
    run(
        "roster rows",
        "DELETE FROM staff_rosters WHERE staff_id = ANY(CAST(:s AS uuid[])) "
        "OR branch_id = ANY(CAST(:b AS uuid[]))",
        s=staff,
        b=branches,
    )
    run(
        "capability cells",
        "DELETE FROM staff_capabilities WHERE staff_id = ANY(CAST(:s AS uuid[])) "
        "OR service_id = ANY(CAST(:sv AS uuid[]))",
        s=staff,
        sv=services,
    )
    run(
        "length options",
        "DELETE FROM service_extensions WHERE service_id = ANY(CAST(:sv AS uuid[]))",
        sv=services,
    )
    run(
        "discounts",
        "DELETE FROM discounts WHERE service_id = ANY(CAST(:sv AS uuid[])) "
        "OR branch_id = ANY(CAST(:b AS uuid[])) "
        "OR name LIKE 'Cart gift %' OR name LIKE 'Preview gift %'",
        sv=services,
        b=branches,
    )
    run("services", "DELETE FROM services WHERE id = ANY(CAST(:sv AS uuid[]))", sv=services)
    run("allocation runs", "DELETE FROM allocation_runs WHERE branch_id = ANY(CAST(:b AS uuid[]))", b=branches)
    run("staff profiles", "DELETE FROM staff WHERE id = ANY(CAST(:s AS uuid[]))", s=staff)

    # A test branch or account something real still points at is left alone.
    blocked_branches = _ids(
        db,
        "SELECT id FROM locations WHERE id = ANY(CAST(:b AS uuid[])) AND ("
        "EXISTS (SELECT 1 FROM staff WHERE staff.branch_id = locations.id) "
        "OR EXISTS (SELECT 1 FROM bookings WHERE bookings.branch_id = locations.id) "
        "OR EXISTS (SELECT 1 FROM services WHERE services.branch_id = locations.id))",
        b=branches,
    )
    deletable_branches = [b for b in branches if b not in set(blocked_branches)]
    if blocked_branches:
        report.append(
            f"  SKIPPED {len(blocked_branches)} test branch(es) still referenced by real rows"
        )
    run(
        "branches",
        "DELETE FROM locations WHERE id = ANY(CAST(:b AS uuid[]))",
        b=deletable_branches,
    )

    blocked_users = _ids(
        db,
        "SELECT id FROM users WHERE id = ANY(CAST(:u AS uuid[])) AND ("
        "EXISTS (SELECT 1 FROM bookings WHERE bookings.customer_id = users.id) "
        "OR EXISTS (SELECT 1 FROM staff WHERE staff.user_id = users.id))",
        u=users,
    )
    deletable_users = [u for u in users if u not in set(blocked_users)]
    if blocked_users:
        report.append(f"  SKIPPED {len(blocked_users)} account(s) still referenced by real rows")
    run("accounts", "DELETE FROM users WHERE id = ANY(CAST(:u AS uuid[]))", u=deletable_users)

    if apply:
        db.commit()
    else:
        db.rollback()
    return report


if __name__ == "__main__":
    apply_changes = "--apply" in sys.argv
    keep_values: set[str] = set()
    for index, arg in enumerate(sys.argv):
        if arg == "--keep" and index + 1 < len(sys.argv):
            keep_values.update(part.strip() for part in sys.argv[index + 1].split(",") if part.strip())

    session = SessionLocal()
    try:
        lines = purge(session, apply=apply_changes, keep=keep_values)
    finally:
        session.close()

    for line in lines:
        print(line)
    if apply_changes:
        print("\nDone - test data removed.")
    else:
        print("\nDry run only - nothing was changed. Re-run with --apply to make it real.")
