"""Print the pending login OTP for a phone number (local dev helper).

Run with: venv/Scripts/python.exe scripts/peek_otp.py [phone_number]

Defaults to the seeded admin phone. Reads the code straight out of Redis,
useful when NOTIFICATION_BACKEND=console and the uvicorn log has scrolled away.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import redis
from sqlalchemy import text

from app.shared.infrastructure.config.settings import settings
from app.shared.infrastructure.database.session import SessionLocal

DEFAULT_PHONE = "07000000000"


def main() -> None:
    phone = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PHONE

    db = SessionLocal()
    row = db.execute(
        text("SELECT id, role, status FROM users WHERE phone_number = :p"),
        {"p": phone},
    ).first()
    db.close()

    if row is None:
        print(f"No user with phone {phone}")
        sys.exit(1)

    user_id, role, status = row
    print(f"user  = {phone} ({role}/{status}, id {user_id})")

    r = redis.from_url(settings.REDIS_URL, decode_responses=True)
    code = r.get(f"otp:register:{user_id}")
    ttl = r.ttl(f"otp:register:{user_id}")
    attempts = r.get(f"otp:attempts:{user_id}")
    cooldown = r.ttl(f"otp:cooldown:{user_id}")

    if code is None:
        print("No pending OTP -- request one via /app/auth/login first")
        if cooldown > 0:
            print(f"(resend cooldown active: {cooldown}s)")
        sys.exit(1)

    print(f"OTP   = {code}  (expires in {ttl}s, failed attempts: {attempts or 0})")
    if cooldown > 0:
        print(f"resend cooldown: {cooldown}s")


if __name__ == "__main__":
    main()
