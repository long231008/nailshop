from fastapi import FastAPI
from routers.auth import router as auth_router
from database import Base, engine
from models.user import User



Base.metadata.create_all(
    bind=engine
)


app = FastAPI()


app.include_router(
    auth_router
)