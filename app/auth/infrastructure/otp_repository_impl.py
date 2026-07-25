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
