"""Savings goals: CRUD and contributions, always scoped to the user."""
import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_UP
from typing import List, Optional

from sqlalchemy import delete, func, insert, select, update

import middleware.service as db
from .models import GoalContribution, SavingsGoal

EDITABLE = {'name', 'target', 'deadline'}


@dataclass
class Goal:
    id: int
    name: str
    target: Decimal
    deadline: Optional[date]
    saved: Decimal

    @property
    def left(self) -> Decimal:
        return max(self.target - self.saved, Decimal('0.00'))

    @property
    def percent(self) -> int:
        return math.floor(self.saved * 100 / self.target) if self.target > 0 else 0

    @property
    def reached(self) -> bool:
        return self.saved >= self.target


def months_left(deadline: date, today: date) -> Optional[int]:
    """Calendar months until the deadline, counting the current one; None when it has passed."""
    if deadline < today:
        return None
    months = (deadline.year - today.year) * 12 + deadline.month - today.month
    return max(1, months + (1 if deadline.day >= today.day else 0))


def monthly_needed(goal: Goal, today: date) -> Optional[Decimal]:
    """How much to put aside per month to reach the goal on time (rounded up to cents)."""
    if goal.deadline is None or goal.reached:
        return None
    months = months_left(goal.deadline, today)
    if months is None:
        return None
    return (goal.left / months).quantize(Decimal('0.01'), rounding=ROUND_UP)


def _goals_query():
    saved = func.coalesce(func.sum(GoalContribution.amount), 0).label('saved')
    return (select(SavingsGoal.id, SavingsGoal.name, SavingsGoal.target, SavingsGoal.deadline, saved)
            .outerjoin(GoalContribution, GoalContribution.goal_id == SavingsGoal.id)
            .group_by(SavingsGoal.id))


def _goal(row) -> Goal:
    return Goal(id=row.id, name=row.name, target=row.target, deadline=row.deadline, saved=Decimal(row.saved))


async def create(user_id: int, name: str, target: Decimal, deadline: Optional[date]) -> int:
    statement = insert(SavingsGoal).values(user_id=user_id, name=name, target=target, deadline=deadline).returning(SavingsGoal.id)
    return await db._execute_scalar(statement)


async def list_for(user_id: int) -> List[Goal]:
    rows = await db._execute_select_rows(_goals_query().where(SavingsGoal.user_id == user_id).order_by(SavingsGoal.id))
    return [_goal(row) for row in rows]


async def get(user_id: int, goal_id: int) -> Optional[Goal]:
    rows = await db._execute_select_rows(_goals_query().where(SavingsGoal.id == goal_id, SavingsGoal.user_id == user_id))
    return _goal(rows[0]) if rows else None


async def contribute(user_id: int, goal_id: int, amount: Decimal) -> bool:
    """Adds a top-up (or a withdrawal when negative) only to the user's own goal."""
    if await get(user_id, goal_id) is None:
        return False
    await db._execute_insert_update_delete_command(insert(GoalContribution).values(goal_id=goal_id, amount=amount))
    return True


async def update_fields(user_id: int, goal_id: int, **values) -> bool:
    if not values or not set(values) <= EDITABLE:
        raise ValueError(f'not editable: {set(values) - EDITABLE}')
    statement = update(SavingsGoal).where(SavingsGoal.id == goal_id, SavingsGoal.user_id == user_id).values(**values)
    return (await db._execute_insert_update_delete_command(statement)).rowcount == 1


async def remove(user_id: int, goal_id: int) -> bool:
    statement = delete(SavingsGoal).where(SavingsGoal.id == goal_id, SavingsGoal.user_id == user_id)
    return (await db._execute_insert_update_delete_command(statement)).rowcount == 1
