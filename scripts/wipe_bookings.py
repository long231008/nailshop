"""Wipe every booking off the book - the clean-slate button for a database
full of trial runs.

Deletes ALL bookings with their legs and payment transactions, whatever their
status or origin, and hands any custom design that was attached back to its
"priced" state so it can be booked again. Accounts, staff, services, branches
and the audit trail stay untouched.

Dry run by default:

    python scripts/wipe_bookings.py

Apply for real:

    python scripts/wipe_bookings.py --apply
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.shared.infrastructure.database.session import SessionLocal  # noqa: E402


def wipe(db, apply: bool = False) -> list[str]:
    report: list[str] = []

    def run(label, sql):
        count = db.execute(text(sql)).rowcount
        report.append(f"{label}: {count}")

    run(
        "designs released for rebooking",
        "UPDATE custom_designs SET status = 'PRICED' WHERE status = 'ACCEPTED' "
        "AND id IN (SELECT custom_design_id FROM booking_details "
        "           WHERE custom_design_id IS NOT NULL)",
    )
    run("payment transactions", "DELETE FROM payment_transactions")
    run("booking legs", "DELETE FROM booking_details")
    run("bookings", "DELETE FROM bookings")

    if apply:
        db.commit()
    else:
        db.rollback()
    return report


if __name__ == "__main__":
    apply_changes = "--apply" in sys.argv
    session = SessionLocal()
    try:
        lines = wipe(session, apply=apply_changes)
    finally:
        session.close()

    for line in lines:
        print(line)
    if apply_changes:
        print("\nDone - the book is empty.")
    else:
        print("\nDry run only - nothing was changed. Re-run with --apply to make it real.")
