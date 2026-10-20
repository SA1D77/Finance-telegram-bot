from sqlalchemy import BigInteger, Column, Date, Enum, ForeignKey, Integer, SmallInteger, String, TIMESTAMP

from accounting.models import COMMENT_MAX_LENGTH, MONEY, Categories, OperationType
from timeutils import utc_now
from user.models import Base, User

FREQUENCIES = ('daily', 'weekly', 'monthly')


class RecurringOperation(Base):
    """A template that creates the same operation on a schedule (dates are local calendar dates)."""
    __tablename__ = 'recurring_operations'

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey(User.id), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey(Categories.id, ondelete='CASCADE'), nullable=False)
    operation_type = Column(Enum(OperationType), nullable=False)
    amount = Column(MONEY, nullable=False)
    comment = Column(String(COMMENT_MAX_LENGTH), nullable=True)
    frequency = Column(String(8), nullable=False)  # one of FREQUENCIES
    # Day of month (1..31) for monthly, weekday (0 = Monday) for weekly: keeps "every 31st" on the 31st
    # after a shorter month.
    anchor_day = Column(SmallInteger, nullable=False)
    next_date = Column(Date, nullable=False)
    remind_days = Column(SmallInteger, nullable=False, default=0)
    reminded_for = Column(Date, nullable=True)  # next_date the reminder was sent for
    created_at = Column(TIMESTAMP, default=utc_now)
