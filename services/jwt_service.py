from jose import jwt
from datetime import datetime, timedelta

from core.config import (
    SECRET_KEY,
    ALGORITHM,
    JWT_EXPIRE_HOURS
)


def create_access_token(
        user_id,
        role
):

    payload = {

        "user_id": user_id,

        "role": role,

        "exp":
        datetime.utcnow()
        +
        timedelta(
            hours=JWT_EXPIRE_HOURS
        )
    }


    return jwt.encode(
        payload,
        SECRET_KEY,
        algorithm=ALGORITHM
    )