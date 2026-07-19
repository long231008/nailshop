from fastapi import FastAPI

from app.shared.infrastructure.database import models  # noqa: F401 (registers all ORM models)

from app.auth.presentation.router import router as auth_router
from app.branches.presentation.router import router as branches_router
from app.discounts.presentation.routers import router as discounts_router
from app.services.presentation.routers import router as services_router
from app.shifts.presentation.routers import router as shifts_router

app = FastAPI(title="Nailshop API")

app.include_router(auth_router, prefix="/app")
app.include_router(branches_router, prefix="/app")
app.include_router(services_router, prefix="/app")
app.include_router(shifts_router, prefix="/app")
app.include_router(discounts_router, prefix="/app")
