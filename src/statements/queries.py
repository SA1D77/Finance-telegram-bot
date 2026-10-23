"""Lookups for statement import, scoped to the user."""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import desc, func, or_, select

from accounting.models import Accounting, Categories
from middleware.service import _execute_scalar


async def already_imported(user_id: int, amount: Decimal, created_at: datetime, merchant: Optional[str]) -> bool:
    """The same payment (amount, minute, merchant) is already recorded: forwarding a statement twice adds nothing."""
    statement = select(func.count(Accounting.id)).where(
        Accounting.user_id == user_id,
        Accounting.quantity == amount,
        Accounting.created_at == created_at,
        Accounting.comment.is_(None) if merchant is None else Accounting.comment == merchant,
    )
    return bool(await _execute_scalar(statement))


async def category_for_merchant(user_id: int, merchant: Optional[str], kind: str) -> Optional[int]:
    """The category the user last chose for this merchant, if it is still active and fits incomes/expenses."""
    if not merchant:
        return None
    statement = (select(Categories.id)
                 .join(Accounting, Accounting.category_id == Categories.id)
                 .where(Accounting.user_id == user_id,
                        func.lower(Accounting.comment) == merchant.lower(),
                        Categories.is_active.is_(True),
                        or_(Categories.kind == kind, Categories.kind.is_(None)))
                 .order_by(desc(Accounting.created_at))
                 .limit(1))
    return await _execute_scalar(statement)
