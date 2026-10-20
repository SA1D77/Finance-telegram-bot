from sqlalchemy import (
    BigInteger, Boolean, Column, Enum, ForeignKey, Integer, Numeric, String, TIMESTAMP, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID

import uuid
import enum
from timeutils import utc_now

from user.models import Base, User

# Amounts up to 999 999 999 999.99, stored exactly (never float).
MONEY = Numeric(14, 2)
CATEGORY_NAME_MAX_LENGTH = 64
COMMENT_MAX_LENGTH = 255


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
    # 'income' or 'expense' limits where the category is offered; NULL: both.
    kind = Column(String(8), nullable=True)


class Accounting(Base):
    __tablename__ = 'accounting'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(BigInteger, ForeignKey(User.id), nullable=False, index=True)
    created_at = Column(TIMESTAMP, default=utc_now)
    quantity = Column(MONEY, nullable=False)
    category_id = Column(Integer, ForeignKey(Categories.id), nullable=False)
    operation_type = Column(Enum(OperationType), nullable=False)
    comment = Column(String(COMMENT_MAX_LENGTH), nullable=True)
    # Set when entered in another currency: quantity is then the converted amount in BASE_CURRENCY.
    original_amount = Column(MONEY, nullable=True)
    original_currency = Column(String(3), nullable=True)
    rate = Column(Numeric(20, 10), nullable=True)
