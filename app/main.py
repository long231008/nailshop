from fastapi import FastAPI

from app.auth.presentation.router import router as auth_router

app = FastAPI(title="Nailshop API")

app.include_router(auth_router)
