import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from app.admin.presentation.routers import router as admin_router
from app.audit_log.presentation.routers import router as audit_log_router
from app.auth.presentation.router import router as auth_router
from app.availability.presentation.routers import router as availability_router
from app.bookings.application.expire_soft_locks import expire_unpaid_soft_locks
from app.bookings.presentation.routers import router as bookings_router
from app.branches.presentation.router import router as branches_router
from app.custom_designs.presentation.routers import router as custom_designs_router
from app.dashboard.presentation.router import router as dashboard_router
from app.discounts.presentation.routers import router as discounts_router
from app.me.presentation.routers import router as me_router
from app.queue.presentation.routers import router as queue_router
from app.services.presentation.routers import router as services_router
from app.shared.infrastructure.cache.redis_client import redis_client
from app.shared.infrastructure.database import models  # noqa: F401 (registers all ORM models)
from app.shared.infrastructure.database.session import SessionLocal
from app.shifts.presentation.routers import router as shifts_router
from app.staff.presentation.routers import router as staff_router
from app.webhooks.presentation.routers import router as webhooks_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

SOFT_LOCK_CHECK_INTERVAL_SECONDS = 60

scheduler = BackgroundScheduler()


def _run_expire_soft_locks_job() -> None:
    db = SessionLocal()
    try:
        expire_unpaid_soft_locks(db, redis_client)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(
        _run_expire_soft_locks_job,
        "interval",
        seconds=SOFT_LOCK_CHECK_INTERVAL_SECONDS,
        id="expire_soft_locks",
    )
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Nailshop API", lifespan=lifespan)

app.include_router(auth_router, prefix="/app")
app.include_router(branches_router, prefix="/app")
app.include_router(services_router, prefix="/app")
app.include_router(shifts_router, prefix="/app")
app.include_router(discounts_router, prefix="/app")
app.include_router(availability_router, prefix="/app")
app.include_router(bookings_router, prefix="/app")
app.include_router(me_router, prefix="/app")
app.include_router(staff_router, prefix="/app")
app.include_router(custom_designs_router, prefix="/app")
app.include_router(queue_router, prefix="/app")
app.include_router(webhooks_router, prefix="/app")
app.include_router(admin_router, prefix="/app")
app.include_router(audit_log_router, prefix="/app")
app.include_router(dashboard_router, prefix="/app")
