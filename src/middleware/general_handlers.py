from .accounting import _get_category, _find_active_category
from exception import (
    CategoryDoesNotExist,
    CategoryAlreadyExist
)


async def category_does_not_exist(category_id: int, user_id: int):
    response = await _get_category(category_id=category_id, user_id=user_id)

    if not response:
        raise CategoryDoesNotExist

    return response


async def category_already_exist(category: str, user_id: int):
    response = await _find_active_category(name=category, user_id=user_id)

    if response:
        raise CategoryAlreadyExist
