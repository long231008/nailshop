import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("app.request")

HEALTH_PATH = "/health"


class HealthExemptTrustedHostMiddleware:
    """The host allow-list, applied everywhere except the health endpoint.

    A load balancer health-checks a container by dialling its address, so the
    probe arrives as `Host: 10.0.1.23:8001` - a host ALLOWED_HOSTS can never
    list, because the address is handed out at deploy time. Guarding /health
    with it fails every probe, the target never turns healthy, and the deploy
    rolls back; an AWS target group cannot send a custom Host header at all,
    so there is no way to configure around it.

    /health answers a constant body and builds no URLs, so a forged Host has
    nothing to poison there. Every other path keeps the full check, which is
    where host-header injection would actually do damage.
    """

    def __init__(self, app: ASGIApp, allowed_hosts: list[str]) -> None:
        self.app = app
        self.guarded = TrustedHostMiddleware(app, allowed_hosts=allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == HEALTH_PATH:
            await self.app(scope, receive, send)
            return
        await self.guarded(scope, receive, send)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = str(uuid.uuid4())
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        response.headers["X-Request-ID"] = request_id
        logger.info(
            "%s %s -> %d (%.1fms) [%s]",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # Browsers ignore HSTS on plain HTTP, so this is safe in local dev and
        # takes effect the moment the API is served over TLS.
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # The API itself serves JSON and uploaded images, so the document is
        # locked down entirely - except the two interactive doc pages, whose
        # HTML loads Swagger/ReDoc assets from jsdelivr.
        if request.url.path in ("/docs", "/redoc"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                "font-src https://cdn.jsdelivr.net; "
                "connect-src 'self'; frame-ancestors 'none'"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; img-src 'self' data:; frame-ancestors 'none'"
            )
        return response
