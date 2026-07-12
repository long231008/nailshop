from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base


# Database URL
DATABASE_URL = (
    "postgresql://username:password@localhost:5432/nailshop"
)


# Tạo kết nối database
engine = create_engine(
    DATABASE_URL
)


# Tạo session
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


# Base class cho Models
Base = declarative_base()



# Dependency injection
def get_db():

    db = SessionLocal()

    try:
        yield db

    finally:
        db.close()