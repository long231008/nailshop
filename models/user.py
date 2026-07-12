from sqlalchemy import Column, Integer, String, Boolean

from database import Base


class User(Base):

    __tablename__ = "users"


    id = Column(
        Integer,
        primary_key=True,
        index=True
    )


    full_name = Column(
        String
    )


    email = Column(
        String,
        unique=True,
        index=True
    )


    password = Column(
        String
    )


    role = Column(
        String,
        default="customer"
    )


    pending_id = Column(
        String,
        nullable=True
    )


    is_active = Column(
        Boolean,
        default=False
    )