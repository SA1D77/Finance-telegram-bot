from sqlalchemy import BigInteger, Column, ForeignKey, Integer, SmallInteger, String, TIMESTAMP, UniqueConstraint

from accounting.models import MONEY, Categories
from timeutils import utc_now
from user.models import Base, User


class Budget(Base):
    """Monthly spending limit: for one category, or for all expenses when category_id is NULL."""
    __tablename__ = 'budgets'
    __table_args__ = (
        # NULLS NOT DISTINCT (PostgreSQL 15+): one overall limit per user, one per category.
        UniqueConstraint('user_id', 'category_id', name='uq_budgets_user_category', postgresql_nulls_not_distinct=True),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey(User.id), nullable=False)
    category_id = Column(Integer, ForeignKey(Categories.id, ondelete='CASCADE'), nullable=True)
    amount = Column(MONEY, nullable=False)


class BudgetAlert(Base):
    """A sent 80% / 100% notification. The unique key is the anti-spam guarantee."""
    __tablename__ = 'budget_alerts'
    __table_args__ = (
        UniqueConstraint('budget_id', 'period', 'threshold', name='uq_budget_alerts_once'),
    )

    id = Column(Integer, primary_key=True)
    budget_id = Column(Integer, ForeignKey(Budget.id, ondelete='CASCADE'), nullable=False)
    period = Column(String(7), nullable=False)  # local month, "2026-10"
    threshold = Column(SmallInteger, nullable=False)  # 80 or 100
    sent_at = Column(TIMESTAMP, nullable=False, default=utc_now)
