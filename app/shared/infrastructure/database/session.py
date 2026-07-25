from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.shared.infrastructure.config.settings import settings

engine = create_engine(settings.DATABASE_URL, echo=True, pool_pre_ping=True)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()
