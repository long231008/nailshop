# app/shared/infrastructure/database/session.py

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.shared.infrastructure.config.settings import settings


# Tạo kết nối tới PostgreSQL
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True
)


# Tạo database session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


# Dependency cho FastAPI
def get_db():
    db = SessionLocal()

    try:
        yield db

    finally:
        db.close()