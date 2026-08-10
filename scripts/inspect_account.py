"""Print everything the database knows about one account - the who/when/what
behind a suspicious row.

    python scripts/inspect_account.py 07551000003

Shows the user row (with its creation time - the single best clue), any staff
profile, every booking with its branch and status, every payment transaction
(the provider id says where money "came from": seed-... rows were planted by
the demo seed script, anything else arrived through the payment webhook), the
audit entries that mention the account, and neighbouring sequential phone
numbers - a run of 07551000001/2/3 means a batch generator, not a human.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.shared.infrastructure.database.session import SessionLocal  # noqa: E402


def inspect(db, phone: str) -> None:
    user = db.execute(
        text(
            "SELECT id, phone_number, email, first_name, surname, role, status, created_at "
            "FROM users WHERE phone_number = :p"
        ),
        {"p": phone},
    ).first()
    if user is None:
        print(f"No account with phone {phone}.")
        return

    print(f"ACCOUNT  {user.first_name or ''} {user.surname or ''}".rstrip())
    print(f"  id: {user.id}")
    print(f"  phone: {user.phone_number}  email: {user.email or '-'}")
    print(f"  role: {user.role}  status: {user.status}")
    print(f"  created_at: {user.created_at}  <-- when this account came into being")

    staff = db.execute(
        text("SELECT id, display_name, status FROM staff WHERE user_id = :u"), {"u": user.id}
    ).first()
    print(f"  staff profile: {staff.display_name} ({staff.status})" if staff else "  staff profile: none")

    print("\nBOOKINGS")
    bookings = db.execute(
        text(
            "SELECT b.id, b.booking_date, b.status, b.total_price, b.deposit_amount, "
            "b.created_at, l.name AS branch "
            "FROM bookings b LEFT JOIN locations l ON l.id = b.branch_id "
            "WHERE b.customer_id = :u ORDER BY b.created_at"
        ),
        {"u": user.id},
    ).all()
    if not bookings:
        print("  none")
    for b in bookings:
        print(
            f"  {b.booking_date} @ {b.branch or '?'} | {b.status} | total £{b.total_price} "
            f"deposit £{b.deposit_amount or 0} | created {b.created_at}"
        )
        for t in db.execute(
            text(
                "SELECT provider_transaction_id, transaction_type, amount, status, created_at "
                "FROM payment_transactions WHERE booking_id = :b ORDER BY created_at"
            ),
            {"b": b.id},
        ):
            origin = "DEMO SEED" if t.provider_transaction_id.startswith("seed-") else "webhook"
            print(
                f"    money: {t.transaction_type} £{t.amount} {t.status} "
                f"[{origin}: {t.provider_transaction_id}] at {t.created_at}"
            )

    print("\nAUDIT ENTRIES mentioning this account")
    audits = db.execute(
        text(
            "SELECT action, details, created_at FROM audit_logs "
            "WHERE actor_user_id = :u OR entity_id = :u "
            "OR entity_id IN (SELECT id FROM bookings WHERE customer_id = :u) "
            "ORDER BY created_at"
        ),
        {"u": user.id},
    ).all()
    if not audits:
        print("  none")
    for a in audits:
        print(f"  {a.created_at} {a.action} {a.details or ''}")

    prefix = phone[:-3]
    print(f"\nNEIGHBOURING NUMBERS ({prefix}xxx) - a run means a batch generator")
    for n in db.execute(
        text(
            "SELECT phone_number, first_name, surname, created_at FROM users "
            "WHERE phone_number LIKE :like AND phone_number != :p ORDER BY phone_number"
        ),
        {"like": prefix + "%", "p": phone},
    ):
        print(f"  {n.phone_number}  {n.first_name or ''} {n.surname or ''}  created {n.created_at}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/inspect_account.py <phone-number>")
        sys.exit(1)
    session = SessionLocal()
    try:
        inspect(session, sys.argv[1])
    finally:
        session.close()
