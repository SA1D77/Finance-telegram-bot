"""Queries for analytics; every one is scoped to the user and a [since, until) range in naive UTC."""
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Tuple

from sqlalchemy import case, desc, func, select

from accounting.models import Accounting, Categories, OperationType
from middleware.service import _execute_select_rows
from timeutils import TIMEZONE_NAME

ZERO = Decimal('0')


def _in_range(statement, user_id: int, since: datetime, until: datetime):
    return statement.where(Accounting.user_id == user_id, Accounting.created_at >= since, Accounting.created_at < until)


async def totals(user_id: int, since: datetime, until: datetime) -> Tuple[Decimal, Decimal]:
    """(income, expense) in the range."""
    income = func.coalesce(func.sum(case((Accounting.operation_type == OperationType.income, Accounting.quantity))), 0)
    expense = func.coalesce(func.sum(case((Accounting.operation_type == OperationType.expenditure, Accounting.quantity))), 0)
    row = (await _execute_select_rows(_in_range(select(income, expense), user_id, since, until)))[0]
    return Decimal(row[0]), Decimal(row[1])


async def expenses_by_category(user_id: int, since: datetime, until: datetime) -> List[Tuple[str, Decimal]]:
    statement = _in_range(
        select(Categories.name, func.sum(Accounting.quantity))
        .join(Categories, Categories.id == Accounting.category_id)
        .where(Accounting.operation_type == OperationType.expenditure)
        .group_by(Categories.name),
        user_id, since, until)
    return [(name, total) for name, total in await _execute_select_rows(statement)]


async def largest_expenses(user_id: int, since: datetime, until: datetime, limit: int = 5):
    statement = _in_range(
        select(Accounting.created_at, Accounting.quantity, Accounting.comment, Categories.name.label('category'))
        .join(Categories, Categories.id == Accounting.category_id)
        .where(Accounting.operation_type == OperationType.expenditure)
        .order_by(desc(Accounting.quantity), desc(Accounting.created_at))
        .limit(limit),
        user_id, since, until)
    return await _execute_select_rows(statement)


async def monthly_totals(user_id: int, since: datetime, until: datetime) -> Dict[Tuple[int, int], Tuple[Decimal, Decimal]]:
    """{(year, month): (income, expense)} by local calendar month."""
    # created_at is naive UTC: mark it as UTC, then convert to the local zone before truncating.
    local = func.timezone(TIMEZONE_NAME, func.timezone('UTC', Accounting.created_at))
    month = func.date_trunc('month', local).label('month')
    income = func.coalesce(func.sum(case((Accounting.operation_type == OperationType.income, Accounting.quantity))), 0)
    expense = func.coalesce(func.sum(case((Accounting.operation_type == OperationType.expenditure, Accounting.quantity))), 0)
    statement = _in_range(select(month, income, expense).group_by(month), user_id, since, until)
    return {(row.month.year, row.month.month): (Decimal(row[1]), Decimal(row[2])) for row in await _execute_select_rows(statement)}
