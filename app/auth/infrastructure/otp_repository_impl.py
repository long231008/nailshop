from uuid import UUID

from redis import Redis

from app.auth.domain.repository import OtpRepository


class RedisOtpRepository(OtpRepository):
    def __init__(self, redis_client: Redis):
        self._redis = redis_client

    def save(self, pending_id: UUID, code: str, ttl_seconds: int) -> None:
        self._redis.set(f"otp:register:{pending_id}", code, ex=ttl_seconds)
