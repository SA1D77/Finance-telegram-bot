from sqlalchemy import (
    BigInteger, Boolean, Column, Enum, ForeignKey, Integer, Numeric, String, TIMESTAMP, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID

import uuid
import enum
from datetime import datetime

from user.models import Base, User

# Amounts up to 999 999 999 999.99, stored exactly (never float).
MONEY = Numeric(14, 2)
CATEGORY_NAME_MAX_LENGTH = 64


class OperationType(enum.Enum):
    income = 'Income'
    expenditure = 'Expenditure'


class Categories(Base):
    __tablename__ = 'categories'
    __table_args__ = (UniqueConstraint('user_id', 'name', name='uq_categories_user_name'),)

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey(User.id), nullable=False)
    name = Column(String(CATEGORY_NAME_MAX_LENGTH), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class Accounting(Base):
    __tablename__ = 'accounting'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(BigInteger, ForeignKey(User.id), nullable=False, index=True)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    quantity = Column(MONEY, nullable=False)
    category_id = Column(Integer, ForeignKey(Categories.id), nullable=False)
    operation_type = Column(Enum(OperationType), nullable=False)
