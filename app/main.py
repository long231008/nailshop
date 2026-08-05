import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.admin.presentation.routers import router as admin_router
from app.allocation.application.nightly import run_nightly_allocation
from app.allocation.presentation.routers import router as allocation_router
from app.audit_log.presentation.routers import router as audit_log_router
from app.auth.presentation.router import router as auth_router
from app.availability.presentation.routers import router as availability_router
from app.bookings.application.expire_soft_locks import expire_unpaid_soft_locks
from app.bookings.presentation.routers import router as bookings_router
from app.branches.presentation.router import router as branches_router
from app.capability.presentation.routers import router as capability_router
from app.custom_designs.presentation.routers import router as custom_designs_router
from app.dashboard.presentation.router import router as dashboard_router
from app.discounts.presentation.routers import router as discounts_router
from app.leaves.presentation.routers import router as leaves_router
from app.me.presentation.routers import router as me_router
from app.notification.infrastructure.senders import get_notification_sender
from app.schedule.presentation.routers import router as schedule_router
from app.services.presentation.routers import router as services_router
from app.shared.infrastructure.cache.redis_client import redis_client
from app.shared.infrastructure.clock import shop_timezone, today_in_shop_tz
from app.shared.infrastructure.config.settings import settings
from app.shared.infrastructure.database import models  # noqa: F401 (registers all ORM models)
from app.shared.infrastructure.database.session import SessionLocal
from app.shared.infrastructure.rate_limit import limiter, rate_limit_exceeded_handler
from app.shared.presentation.middleware import (
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
)
from app.shifts.presentation.routers import router as shifts_router
from app.slot_locks.presentation.routers import router as slot_locks_router
from app.staff.presentation.routers import router as staff_router
from app.webhooks.presentation.routers import router as webhooks_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

SOFT_LOCK_CHECK_INTERVAL_SECONDS = 60
# Slightly shorter than the interval so a crashed holder frees the lock by itself.
SOFT_LOCK_JOB_LOCK_TTL_SECONDS = 55

scheduler = BackgroundScheduler()


def _run_expire_soft_locks_job() -> None:
    # Several replicas each run a scheduler; the Redis lock lets one of them do the
    # sweep per tick instead of all of them at once.
    if not redis_client.set(
        "jobs:expire_soft_locks:lock", "1", nx=True, ex=SOFT_LOCK_JOB_LOCK_TTL_SECONDS
    ):
        return
    db = SessionLocal()
    try:
        expire_unpaid_soft_locks(db, redis_client, get_notification_sender())
    finally:
        db.close()


def _run_nightly_allocation_job() -> None:
    # The 21:00 close (design doc 2.1): tomorrow's bookings freeze by the clock;
    # this run assigns a technician to every any-tech booking. A short "running"
    # lock keeps replicas from racing; the "done" marker is only written after
    # success, so the 21:20 watchdog reruns a crashed job - and materialize
    # itself is idempotent (fix #8).
    target = today_in_shop_tz() + timedelta(days=1)
    done_key = f"jobs:nightly_allocation:done:{target.isoformat()}"
    if redis_client.get(done_key):
        return
    if not redis_client.set("jobs:nightly_allocation:running", "1", nx=True, ex=900):
        return
    db = SessionLocal()
    try:
        run_nightly_allocation(db)
        redis_client.set(done_key, "1", ex=24 * 3600)
    finally:
        db.close()
        redis_client.delete("jobs:nightly_allocation:running")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Say it loudly at boot rather than discovering it after an incident.
    for problem in settings.insecure_wildcards:
        logging.getLogger("app.startup").warning("INSECURE FOR PRODUCTION: %s", problem)

    scheduler.add_job(
        _run_expire_soft_locks_job,
        "interval",
        seconds=SOFT_LOCK_CHECK_INTERVAL_SECONDS,
        id="expire_soft_locks",
    )
    scheduler.add_job(
        _run_nightly_allocation_job,
        "cron",
        hour=settings.BOOKING_CLOSE_HOUR,
        minute=0,
        timezone=str(shop_timezone()),
        id="nightly_allocation",
    )
    # Watchdog 20 minutes later: if the 21:00 run crashed mid-way, the Redis key
    # holder is gone but assignments are idempotent, so run again (doc fix #8).
    scheduler.add_job(
        _run_nightly_allocation_job,
        "cron",
        hour=settings.BOOKING_CLOSE_HOUR,
        minute=20,
        timezone=str(shop_timezone()),
        id="nightly_allocation_watchdog",
    )
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Nailshop API", lifespan=lifespan)

UPLOADS_DIR = Path(__file__).resolve().parents[1] / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# add_middleware() prepends, so runtime order is the reverse of this list:
# RequestLogging -> CORS -> SlowAPI -> SecurityHeaders -> TrustedHost -> app.
# CORS must sit OUTSIDE SlowAPI so a middleware-produced 429 still carries
# CORS headers (a browser cannot read the error otherwise) and preflight
# OPTIONS requests do not consume rate-limit budget.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts_list)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=settings.cors_allowed_origins_list != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)


@app.get("/health")
def health_check() -> dict:
    return {"status": "ok"}


app.include_router(auth_router, prefix="/app")
app.include_router(branches_router, prefix="/app")
app.include_router(services_router, prefix="/app")
app.include_router(shifts_router, prefix="/app")
app.include_router(slot_locks_router, prefix="/app")
app.include_router(leaves_router, prefix="/app")
app.include_router(schedule_router, prefix="/app")
app.include_router(discounts_router, prefix="/app")
app.include_router(availability_router, prefix="/app")
app.include_router(bookings_router, prefix="/app")
app.include_router(capability_router, prefix="/app")
app.include_router(allocation_router, prefix="/app")
app.include_router(me_router, prefix="/app")
app.include_router(staff_router, prefix="/app")
app.include_router(custom_designs_router, prefix="/app")
app.include_router(webhooks_router, prefix="/app")
app.include_router(admin_router, prefix="/app")
app.include_router(audit_log_router, prefix="/app")
app.include_router(dashboard_router, prefix="/app")
