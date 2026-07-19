from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from app.shared.infrastructure.database import models  # noqa: F401 (registers all ORM models)

from app.admin.presentation.routers import router as admin_router
from app.audit_log.presentation.routers import router as audit_log_router
from app.auth.presentation.router import router as auth_router
from app.availability.presentation.routers import router as availability_router
from app.bookings.presentation.routers import router as bookings_router
from app.branches.presentation.router import router as branches_router
from app.custom_designs.presentation.routers import router as custom_designs_router
from app.discounts.presentation.routers import router as discounts_router
from app.me.presentation.routers import router as me_router
from app.queue.presentation.routers import router as queue_router
from app.services.presentation.routers import router as services_router
from app.shifts.presentation.routers import router as shifts_router
from app.staff.presentation.routers import router as staff_router
from app.webhooks.presentation.routers import router as webhooks_router

app = FastAPI(title="Nailshop API")

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

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
