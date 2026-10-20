from sqlalchemy import BigInteger, Column, Date, ForeignKey, Integer, String, TIMESTAMP

from accounting.models import MONEY
from timeutils import utc_now
from user.models import Base, User

GOAL_NAME_MAX_LENGTH = 64


class SavingsGoal(Base):
    """Something to save for. Money put aside is tracked separately and does not change the balance."""
    __tablename__ = 'savings_goals'

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey(User.id), nullable=False, index=True)
    name = Column(String(GOAL_NAME_MAX_LENGTH), nullable=False)
    target = Column(MONEY, nullable=False)
    deadline = Column(Date, nullable=True)
    created_at = Column(TIMESTAMP, default=utc_now)


class GoalContribution(Base):
    """A top-up (positive) or a withdrawal (negative)."""
    __tablename__ = 'goal_contributions'

    id = Column(Integer, primary_key=True)
    goal_id = Column(Integer, ForeignKey(SavingsGoal.id, ondelete='CASCADE'), nullable=False, index=True)
    amount = Column(MONEY, nullable=False)
    created_at = Column(TIMESTAMP, default=utc_now)
