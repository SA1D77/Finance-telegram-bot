import uuid
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import case, func, insert, select, update, desc
from sqlalchemy.dialects.postgresql import insert as pg_insert

from datetime import datetime, timedelta

from accounting.models import Categories, Accounting, OperationType
from timeutils import utc_now
from .service import (
    _execute_insert_update_delete_command,
    _execute_scalar,
    _execute_select_command,
    _execute_select_rows,
)


async def _add_new_category(category_name: str, user_id: int):
    """Creates the category, or re-enables it if it was deleted before."""
    statemant = (pg_insert(Categories)
                 .values(name=category_name, user_id=user_id, is_active=True)
                 .on_conflict_do_update(constraint='uq_categories_user_name', set_={'is_active': True}))
    await _execute_insert_update_delete_command(statemant)


async def _show_all_categories(user_id: int) -> List[Categories]:
    """Active categories of the user in the order they were created."""
    statemant = (select(Categories)
                 .where(Categories.user_id == user_id, Categories.is_active.is_(True))
                 .order_by(Categories.id))
    return await _execute_select_command(statemant)


async def _get_category(category_id: int, user_id: int) -> Optional[Categories]:
    """The category only if it is active and belongs to this user."""
    statemant = select(Categories).where(
        Categories.id == category_id, Categories.user_id == user_id, Categories.is_active.is_(True)
    )
    response = await _execute_select_command(statemant)
    return response[0] if response else None


async def _find_active_category(name: str, user_id: int) -> Optional[Categories]:
    statemant = select(Categories).where(
        Categories.name == name, Categories.user_id == user_id, Categories.is_active.is_(True)
    )
    response = await _execute_select_command(statemant)
    return response[0] if response else None


async def _delete_category(user_id: int, category_id: int):
    """Soft delete: operations keep pointing at the category."""
    statemant = (update(Categories)
                 .where(Categories.user_id == user_id, Categories.id == category_id)
                 .values(is_active=False))
    await _execute_insert_update_delete_command(statemant)


async def _get_balance(user_id: int) -> Decimal:
    """Computed from the operations, so it can never drift from them."""
    signed = case((Accounting.operation_type == OperationType.income, Accounting.quantity), else_=-Accounting.quantity)
    statemant = select(func.coalesce(func.sum(signed), 0)).where(Accounting.user_id == user_id)
    return Decimal(await _execute_scalar(statemant))


async def _add_operation(user_id: int, category_id: int, quantity: Decimal, operation_type: OperationType,
                         comment: Optional[str] = None, created_at: Optional[datetime] = None) -> uuid.UUID:
    """Returns the new operation id (used by the undo button). created_at is naive UTC, default now."""
    statemant = insert(Accounting).values(
        user_id=user_id,
        quantity=quantity,
        category_id=category_id,
        operation_type=operation_type,
        comment=comment,
        created_at=created_at or utc_now(),
    ).returning(Accounting.id)
    return await _execute_scalar(statemant)


async def _add_income(user_id: int, category_id: int, quantity: Decimal) -> uuid.UUID:
    return await _add_operation(user_id=user_id, category_id=category_id, quantity=quantity, operation_type=OperationType.income)


async def _add_expenditure(user_id: int, category_id: int, quantity: Decimal) -> uuid.UUID:
    return await _add_operation(user_id=user_id, category_id=category_id, quantity=quantity, operation_type=OperationType.expenditure)


async def _get_accounts_between(user_id: int, since: Optional[datetime], until: Optional[datetime]):
    """Report rows (see _get_accounts) in [since, until); None means open-ended. Oldest first."""
    statement = (select(Accounting.created_at, Accounting.quantity, Accounting.operation_type, Accounting.comment,
                        Categories.name.label('categories'))
                 .join(Categories, Categories.id == Accounting.category_id)
                 .where(Accounting.user_id == user_id))
    if since is not None:
        statement = statement.where(Accounting.created_at >= since)
    if until is not None:
        statement = statement.where(Accounting.created_at < until)
    return await _execute_select_rows(statement.order_by(Accounting.created_at))


async def _get_accounts(user_id: int, time: str):
    """Rows with created_at, quantity, operation_type, comment and the category name as `categories`."""
    statement = (select(Accounting.created_at, Accounting.quantity, Accounting.operation_type, Accounting.comment,
                        Categories.name.label('categories'))
                 .join(Categories, Categories.id == Accounting.category_id)
                 .where(Accounting.user_id == user_id))
    if time == 'last':
        statement = statement.order_by(desc(Accounting.created_at)).limit(10)
    else:
        threshold_date = utc_now() - timedelta(days=int(time))
        statement = statement.where(Accounting.created_at >= threshold_date).order_by(Accounting.created_at)
    return await _execute_select_rows(statement)
