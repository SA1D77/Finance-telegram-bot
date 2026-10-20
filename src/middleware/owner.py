from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User


class OwnerOnlyMiddleware(BaseMiddleware):
    """Drops every update that does not come from the owner.

    Registered as an outer middleware on Dispatcher.update, so foreign updates
    never reach filters, handlers, the database or Redis, and get no reply.
    Updates without a user (channel posts and the like) are dropped too.
    """

    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user: Optional[User] = data.get('event_from_user')
        if user is None or user.id != self.owner_id:
            return None
        return await handler(event, data)
