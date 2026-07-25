from redis import Redis

from app.shared.infrastructure.config.settings import settings

redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)


def get_redis() -> Redis:
    return redis_client
