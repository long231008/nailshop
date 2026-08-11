"""Erase the entire audit trail - every entry, money records included.

The dashboard's delete buttons deliberately keep payment and cancellation
entries; this script is the owner's total-reset tool and keeps nothing.
Meant for clearing trial-run noise before going live - after real customers
pay real deposits, prefer the dashboard's retention tools instead.

Dry run by default:

    python scripts/wipe_audit.py

Apply for real:

    python scripts/wipe_audit.py --apply
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.shared.infrastructure.database.session import SessionLocal  # noqa: E402

if __name__ == "__main__":
    apply_changes = "--apply" in sys.argv
    session = SessionLocal()
    try:
        count = session.execute(text("DELETE FROM audit_logs")).rowcount
        if apply_changes:
            session.commit()
        else:
            session.rollback()
    finally:
        session.close()

    print(f"audit entries: {count}")
    if apply_changes:
        print("\nDone - the audit trail is empty.")
    else:
        print("\nDry run only - nothing was changed. Re-run with --apply to make it real.")
