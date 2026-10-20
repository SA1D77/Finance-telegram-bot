from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from user.models import User
from .service import _execute_insert_update_delete_command, _execute_scalar
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
