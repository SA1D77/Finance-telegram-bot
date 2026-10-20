"""Recurring templates: CRUD scoped to the user, and the scheduled run used by Celery beat."""
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, List, Optional

from sqlalchemy import delete, insert, select, update

import middleware.service as db
from accounting.models import Accounting, Categories, OperationType
from timeutils import LOCAL_TZ, to_utc_naive
from .models import RecurringOperation
from .schedule import advance, due_dates, reminder_due

EDITABLE = {'amount', 'next_date', 'comment', 'remind_days'}
CREATED_AT_LOCAL = time(12, 0)  # scheduled operations are dated at local noon


def _columns():
    return (RecurringOperation.id, RecurringOperation.user_id, RecurringOperation.category_id,
            RecurringOperation.operation_type, RecurringOperation.amount, RecurringOperation.comment,
            RecurringOperation.frequency, RecurringOperation.anchor_day, RecurringOperation.next_date,
            RecurringOperation.remind_days, RecurringOperation.reminded_for, Categories.name.label('category'))


def _with_category():
    return select(*_columns()).join(Categories, Categories.id == RecurringOperation.category_id)


async def create(user_id: int, category_id: int, operation_type: OperationType, amount: Decimal, comment: Optional[str],
                 frequency: str, anchor_day: int, next_date: date, remind_days: int) -> int:
    statement = insert(RecurringOperation).values(
        user_id=user_id, category_id=category_id, operation_type=operation_type, amount=amount, comment=comment,
        frequency=frequency, anchor_day=anchor_day, next_date=next_date, remind_days=remind_days,
    ).returning(RecurringOperation.id)
    return await db._execute_scalar(statement)


async def list_for(user_id: int) -> List[Any]:
    statement = (_with_category()
                 .where(RecurringOperation.user_id == user_id)
                 .order_by(RecurringOperation.next_date, RecurringOperation.id))
    return await db._execute_select_rows(statement)


async def get(user_id: int, template_id: int) -> Optional[Any]:
    rows = await db._execute_select_rows(
        _with_category().where(RecurringOperation.id == template_id, RecurringOperation.user_id == user_id))
    return rows[0] if rows else None


async def update_fields(user_id: int, template_id: int, **values) -> bool:
    if not values or not set(values) <= EDITABLE:
        raise ValueError(f'not editable: {set(values) - EDITABLE}')
    if 'next_date' in values:
        values['reminded_for'] = None  # a moved occurrence deserves its own reminder
    statement = (update(RecurringOperation)
                 .where(RecurringOperation.id == template_id, RecurringOperation.user_id == user_id)
                 .values(**values))
    return (await db._execute_insert_update_delete_command(statement)).rowcount == 1


async def skip_next(user_id: int, template_id: int) -> Optional[date]:
    """Moves the template past its next occurrence without creating it; returns the new date."""
    row = await get(user_id, template_id)
    if row is None:
        return None
    new_date = advance(row.next_date, row.frequency, row.anchor_day)
    statement = (update(RecurringOperation)
                 .where(RecurringOperation.id == template_id, RecurringOperation.user_id == user_id,
                        RecurringOperation.next_date == row.next_date)
                 .values(next_date=new_date, reminded_for=None))
    if (await db._execute_insert_update_delete_command(statement)).rowcount != 1:
        return None
    return new_date


async def remove(user_id: int, template_id: int) -> bool:
    statement = delete(RecurringOperation).where(RecurringOperation.id == template_id, RecurringOperation.user_id == user_id)
    return (await db._execute_insert_update_delete_command(statement)).rowcount == 1


@dataclass
class Created:
    template: Any
    operation_id: Any
    day: date


async def run_due(today: date) -> List[Created]:
    """Creates every occurrence due up to `today` (local date) and moves the templates on.

    The template row is advanced with a compare-and-set on next_date inside the same transaction
    as the inserts, so two overlapping runs can never create an occurrence twice.
    """
    due = await db._execute_select_rows(_with_category().where(RecurringOperation.next_date <= today))
    created: List[Created] = []
    for row in due:
        days, new_next = due_dates(row.next_date, today, row.frequency, row.anchor_day)
        async with db.async_sessioin_maker() as session:
            async with session.begin():
                moved = await session.execute(
                    update(RecurringOperation)
                    .where(RecurringOperation.id == row.id, RecurringOperation.next_date == row.next_date)
                    .values(next_date=new_next, reminded_for=None)
                    .returning(RecurringOperation.id))
                if moved.scalar() is None:
                    continue  # another run got here first
                for day in days:
                    result = await session.execute(insert(Accounting).values(
                        user_id=row.user_id, category_id=row.category_id, operation_type=row.operation_type,
                        quantity=row.amount, comment=row.comment,
                        created_at=to_utc_naive(datetime.combine(day, CREATED_AT_LOCAL, tzinfo=LOCAL_TZ)),
                    ).returning(Accounting.id))
                    created.append(Created(template=row, operation_id=result.scalar(), day=day))
    return created


async def reminders_due(today: date) -> List[Any]:
    """Templates whose reminder should go out today; each is marked so it is sent once."""
    rows = await db._execute_select_rows(_with_category().where(RecurringOperation.remind_days > 0))
    result = []
    for row in rows:
        if not reminder_due(row.next_date, row.remind_days, today, row.reminded_for):
            continue
        statement = (update(RecurringOperation)
                     .where(RecurringOperation.id == row.id, RecurringOperation.reminded_for.is_distinct_from(row.next_date))
                     .values(reminded_for=row.next_date))
        if (await db._execute_insert_update_delete_command(statement)).rowcount == 1:
            result.append(row)
    return result
