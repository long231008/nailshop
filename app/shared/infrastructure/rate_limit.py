from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.shared.infrastructure.config.settings import settings


def client_ip(request: Request) -> str:
    """Rate-limit key: the real client address.

    X-Forwarded-For is trivially spoofable, so it is only honoured when the app is
    explicitly told it sits behind a proxy it controls. The left-most entry is the
    original client as recorded by that proxy.
    """
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=client_ip, default_limits=["100/minute"])


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."},
    )
