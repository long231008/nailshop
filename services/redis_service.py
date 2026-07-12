import redis


redis_client = redis.Redis(
    host="localhost",
    port=6379,
    db=0,
    decode_responses=True
)


def save_otp(pending_id: str, otp: str):

    redis_client.setex(
        f"otp:{pending_id}",
        300,
        otp
    )


def get_otp(pending_id: str):

    return redis_client.get(
        f"otp:{pending_id}"
    )


def delete_otp(pending_id: str):

    redis_client.delete(
        f"otp:{pending_id}"
    )


def save_pending_user(
        pending_id: str,
        data: str
):

    redis_client.setex(
        f"user:{pending_id}",
        300,
        data
    )