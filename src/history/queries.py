"""Operation history. Every query is scoped to the user; ids from callbacks are never trusted alone."""
import uuid
from datetime import datetime
from typing import Any, List, Optional, Tuple

from sqlalchemy import delete, desc, func, select, update

from accounting.models import Accounting, Categories
from middleware.service import (
    _execute_insert_update_delete_command,
    _execute_scalar,
    _execute_select_rows,
)

EDITABLE_FIELDS = {'quantity', 'category_id', 'comment', 'created_at'}


def _columns():
    return (Accounting.id, Accounting.created_at, Accounting.quantity, Accounting.operation_type,
            Accounting.comment, Accounting.category_id, Categories.name.label('category'))


def _filtered(statement, user_id: int, category_id: Optional[int], since: Optional[datetime], until: Optional[datetime]):
    statement = statement.where(Accounting.user_id == user_id)
    if category_id is not None:
        statement = statement.where(Accounting.category_id == category_id)
    if since is not None:
        statement = statement.where(Accounting.created_at >= since)
    if until is not None:
        statement = statement.where(Accounting.created_at < until)
    return statement


async def list_operations(user_id: int, offset: int, limit: int, category_id: Optional[int] = None,
                          since: Optional[datetime] = None, until: Optional[datetime] = None) -> Tuple[List[Any], int]:
    """A page of operations, newest first, and the total count for the same filter."""
    rows_statement = _filtered(
        select(*_columns()).join(Categories, Categories.id == Accounting.category_id),
        user_id, category_id, since, until,
    ).order_by(desc(Accounting.created_at), desc(Accounting.id)).offset(offset).limit(limit)
    count_statement = _filtered(select(func.count(Accounting.id)), user_id, category_id, since, until)

    return await _execute_select_rows(rows_statement), await _execute_scalar(count_statement)


async def get_operation(user_id: int, operation_id: uuid.UUID) -> Optional[Any]:
    statement = (select(*_columns())
                 .join(Categories, Categories.id == Accounting.category_id)
                 .where(Accounting.id == operation_id, Accounting.user_id == user_id))
    rows = await _execute_select_rows(statement)
    return rows[0] if rows else None


async def last_operation(user_id: int) -> Optional[Any]:
    rows, _ = await list_operations(user_id, offset=0, limit=1)
    return rows[0] if rows else None


async def update_operation(user_id: int, operation_id: uuid.UUID, **values) -> bool:
    if not values or not set(values) <= EDITABLE_FIELDS:
        raise ValueError(f'not editable: {set(values) - EDITABLE_FIELDS}')
    statement = (update(Accounting)
                 .where(Accounting.id == operation_id, Accounting.user_id == user_id)
                 .values(**values))
    result = await _execute_insert_update_delete_command(statement)
    return result.rowcount == 1


async def delete_operation(user_id: int, operation_id: uuid.UUID) -> bool:
    statement = delete(Accounting).where(Accounting.id == operation_id, Accounting.user_id == user_id)
    result = await _execute_insert_update_delete_command(statement)
    return result.rowcount == 1


async def used_categories(user_id: int) -> List[Any]:
    """Categories that have operations (deleted ones included), for the history filter."""
    statement = (select(Categories.id, Categories.name)
                 .join(Accounting, Accounting.category_id == Categories.id)
                 .where(Categories.user_id == user_id)
                 .group_by(Categories.id, Categories.name)
                 .order_by(Categories.name))
    return await _execute_select_rows(statement)
