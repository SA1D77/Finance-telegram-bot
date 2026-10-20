"""Monthly budgets: limits, spending in the current local month, alert bookkeeping."""
import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from accounting.models import Accounting, Categories, OperationType
from middleware.service import _execute_insert_update_delete_command, _execute_scalar, _execute_select_rows
from timeutils import LOCAL_TZ, local_now, to_utc_naive
from .models import Budget, BudgetAlert

THRESHOLDS = (80, 100)


@dataclass
class BudgetStatus:
    budget_id: int
    category_id: Optional[int]  # None: the overall limit
    name: Optional[str]
    limit: Decimal
    spent: Decimal

    @property
    def percent(self) -> int:
        return math.floor(self.spent * 100 / self.limit) if self.limit > 0 else 0

    @property
    def left(self) -> Decimal:
        return self.limit - self.spent


def month_bounds(now: Optional[datetime] = None) -> Tuple[str, datetime, datetime]:
    """("2026-10", since, until) for the local month; bounds as naive UTC."""
    now = now or local_now()
    start = datetime(now.year, now.month, 1, tzinfo=LOCAL_TZ)
    end = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=LOCAL_TZ)
    return f'{now.year:04d}-{now.month:02d}', to_utc_naive(start), to_utc_naive(end)


def days_left(now: Optional[datetime] = None) -> int:
    """Days remaining in the local month, today included."""
    now = now or local_now()
    _, since, until = month_bounds(now)
    return (until - since).days - now.day + 1


async def set_budget(user_id: int, category_id: Optional[int], amount: Decimal) -> None:
    statement = (insert(Budget)
                 .values(user_id=user_id, category_id=category_id, amount=amount)
                 .on_conflict_do_update(constraint='uq_budgets_user_category', set_={'amount': amount}))
    await _execute_insert_update_delete_command(statement)


async def delete_budget(user_id: int, category_id: Optional[int]) -> bool:
    condition = Budget.category_id.is_(None) if category_id is None else Budget.category_id == category_id
    result = await _execute_insert_update_delete_command(delete(Budget).where(Budget.user_id == user_id, condition))
    return result.rowcount > 0


async def _spending(user_id: int, since: datetime, until: datetime) -> Dict[Optional[int], Decimal]:
    """Expenses per category in [since, until), plus the total under the key None."""
    statement = (select(Accounting.category_id, func.sum(Accounting.quantity))
                 .where(Accounting.user_id == user_id,
                        Accounting.operation_type == OperationType.expenditure,
                        Accounting.created_at >= since, Accounting.created_at < until)
                 .group_by(Accounting.category_id))
    spent: Dict[Optional[int], Decimal] = {category_id: total for category_id, total in await _execute_select_rows(statement)}
    spent[None] = sum(spent.values(), Decimal('0'))
    return spent


async def budget_statuses(user_id: int, now: Optional[datetime] = None) -> List[BudgetStatus]:
    """All limits with this month's spending; the overall one first, then by category name."""
    _, since, until = month_bounds(now)
    statement = (select(Budget.id, Budget.category_id, Categories.name, Budget.amount)
                 .outerjoin(Categories, Categories.id == Budget.category_id)
                 .where(Budget.user_id == user_id)
                 .order_by(Budget.category_id.is_not(None), Categories.name))
    rows = await _execute_select_rows(statement)
    spent = await _spending(user_id, since, until)
    return [BudgetStatus(budget_id=row.id, category_id=row.category_id, name=row.name, limit=row.amount,
                         spent=spent.get(row.category_id, Decimal('0'))) for row in rows]


async def record_alert(budget_id: int, period: str, threshold: int) -> bool:
    """True only the first time for this budget, month and threshold (safe against races)."""
    statement = (insert(BudgetAlert)
                 .values(budget_id=budget_id, period=period, threshold=threshold)
                 .on_conflict_do_nothing(constraint='uq_budget_alerts_once')
                 .returning(BudgetAlert.id))
    return await _execute_scalar(statement) is not None


async def new_alerts(user_id: int, now: Optional[datetime] = None) -> List[Tuple[BudgetStatus, int]]:
    """Budgets that crossed a threshold not yet announced this month, with the highest such threshold.

    Every crossed threshold is recorded, so jumping straight past 100% sends one message, not two.
    """
    period, _, _ = month_bounds(now)
    alerts = []
    for status in await budget_statuses(user_id, now):
        fresh = [t for t in THRESHOLDS if status.percent >= t and await record_alert(status.budget_id, period, t)]
        if fresh:
            alerts.append((status, max(fresh)))
    return alerts
