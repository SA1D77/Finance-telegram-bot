from typing import Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from user.models import User
from .service import _execute_insert_update_delete_command, _execute_scalar, _execute_select_rows
from exception import EmailAlreadyExist


async def ensure_user(user_id: int, username: Optional[str]) -> bool:
    """Creates the user row if needed and keeps the username current. True if created."""
    statemant = (insert(User)
                 .values(id=user_id, username=username)
                 .on_conflict_do_nothing(index_elements=[User.id])
                 .returning(User.id))
    created = await _execute_scalar(statemant)
    if created is not None:
        return True

    statemant = (update(User)
                 .where(User.id == user_id, User.username.is_distinct_from(username))
                 .values(username=username))
    await _execute_insert_update_delete_command(statemant)
    return False


async def get_settings(user_id: int) -> Tuple[Optional[str], Optional[str]]:
    """(locale, currency) of the user; None for what has not been chosen yet."""
    rows = await _execute_select_rows(select(User.locale, User.currency).where(User.id == user_id))
    return (rows[0].locale, rows[0].currency) if rows else (None, None)


async def set_currency(user_id: int, currency: str) -> None:
    await _execute_insert_update_delete_command(update(User).where(User.id == user_id).values(currency=currency))


async def _language_locale(user_id: int, lang: str):
    statemant = update(User).where(User.id == user_id).values(locale=lang)
    await _execute_insert_update_delete_command(statemant)


async def _get_user_lang(user_id: int) -> Optional[str]:
    return await _execute_scalar(select(User.locale).where(User.id == user_id))


async def _get_users_email(user_id: int) -> Optional[str]:
    return await _execute_scalar(select(User.email).where(User.id == user_id))


async def _insert_user_email(user_id: int, email: str):
    if await _get_users_email(user_id=user_id):
        raise EmailAlreadyExist

    statemant = update(User).where(User.id == user_id).values(email=email)
    await _execute_insert_update_delete_command(statemant)
