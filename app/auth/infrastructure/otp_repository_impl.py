from uuid import UUID

from redis import Redis

from app.auth.domain.repository import OtpRepository


class RedisOtpRepository(OtpRepository):
    def __init__(self, redis_client: Redis):
        self._redis = redis_client

    def _code_key(self, pending_id: UUID) -> str:
        return f"otp:register:{pending_id}"

    def _attempts_key(self, pending_id: UUID) -> str:
        return f"otp:attempts:{pending_id}"

    def _cooldown_key(self, pending_id: UUID) -> str:
        return f"otp:cooldown:{pending_id}"

    def save(self, pending_id: UUID, code: str, ttl_seconds: int) -> None:
        self._redis.set(self._code_key(pending_id), code, ex=ttl_seconds)
        # A fresh code starts with a clean slate of guesses.
        self._redis.delete(self._attempts_key(pending_id))

    def get(self, pending_id: UUID) -> str | None:
        return self._redis.get(self._code_key(pending_id))

    def delete(self, pending_id: UUID) -> None:
        self._redis.delete(self._code_key(pending_id), self._attempts_key(pending_id))

    def register_failed_attempt(self, pending_id: UUID, ttl_seconds: int) -> int:
        key = self._attempts_key(pending_id)
        attempts = self._redis.incr(key)
        if attempts == 1:
            self._redis.expire(key, ttl_seconds)
        return int(attempts)

    def start_resend_cooldown(self, pending_id: UUID, ttl_seconds: int) -> None:
        self._redis.set(self._cooldown_key(pending_id), "1", ex=ttl_seconds)

    def is_resend_blocked(self, pending_id: UUID) -> bool:
        return self._redis.exists(self._cooldown_key(pending_id)) > 0

    def _email_change_key(self, user_id: UUID) -> str:
        return f"email_change:{user_id}"

    def save_email_change(self, user_id: UUID, email: str, code: str, ttl_seconds: int) -> None:
        # The address only reaches the user row once the code comes back.
        self._redis.hset(self._email_change_key(user_id), mapping={"email": email, "code": code})
        self._redis.expire(self._email_change_key(user_id), ttl_seconds)
        self._redis.delete(self._email_change_attempts_key(user_id))

    def get_email_change(self, user_id: UUID) -> tuple[str, str] | None:
        data = self._redis.hgetall(self._email_change_key(user_id))
        if not data or "email" not in data or "code" not in data:
            return None
        return data["email"], data["code"]

    def delete_email_change(self, user_id: UUID) -> None:
        self._redis.delete(self._email_change_key(user_id))
        self._redis.delete(self._email_change_attempts_key(user_id))

    def _email_change_attempts_key(self, user_id: UUID) -> str:
        return f"email_change_attempts:{user_id}"

    def register_email_change_attempt(self, user_id: UUID, ttl_seconds: int) -> int:
        key = self._email_change_attempts_key(user_id)
        attempts = self._redis.incr(key)
        if attempts == 1:
            self._redis.expire(key, ttl_seconds)
        return attempts
