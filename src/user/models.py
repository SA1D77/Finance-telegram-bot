from sqlalchemy import BigInteger, String, TIMESTAMP, Column
from sqlalchemy.orm import DeclarativeBase

from datetime import datetime


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'user'

    # Numeric Telegram user id: unlike the username it never changes.
    id = Column(BigInteger, primary_key=True, autoincrement=False)
    username = Column(String, nullable=True)
    registered_at = Column(TIMESTAMP, default=datetime.utcnow)
    email = Column(String, nullable=True, unique=True)
    locale = Column(String, nullable=True)
