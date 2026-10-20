from typing import Any, Awaitable, Callable, Dict, Optional, Set

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from middleware.user import ensure_user


class EnsureUserMiddleware(BaseMiddleware):
    """Makes sure the user row exists before any handler touches the database.

    Runs after OwnerOnlyMiddleware, so only the owner gets here. Each user is
    checked once per process; `user_created` tells /start to greet a new user.
    """

    def __init__(self) -> None:
        self.known: Set[int] = set()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user: Optional[User] = data.get('event_from_user')
        if user is not None and user.id not in self.known:
            data['user_created'] = await ensure_user(user_id=user.id, username=user.username)
            self.known.add(user.id)
        return await handler(event, data)
