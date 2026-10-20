import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Deque, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User


class AccessMiddleware(BaseMiddleware):
    """Who may use the bot.

    public=False: only the owner; everyone else is dropped silently (the original personal mode).
    public=True: everyone except other bots. Each user's data is separate in every query.
    Registered as an outer middleware on Dispatcher.update, before anything touches the database.
    """

    def __init__(self, owner_id: int, public: bool = False) -> None:
        self.owner_id = owner_id
        self.public = public

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user: Optional[User] = data.get('event_from_user')
        if user is None or user.is_bot:
            return None
        if user.id != self.owner_id and not self.public:
            return None
        data['is_owner'] = user.id == self.owner_id
        return await handler(event, data)


# The personal-mode name used by earlier code and tests.
def OwnerOnlyMiddleware(owner_id: int) -> AccessMiddleware:  # noqa: N802
    return AccessMiddleware(owner_id=owner_id, public=False)


class ThrottleMiddleware(BaseMiddleware):
    """Drops updates from a user who sends more than `limit` in `window` seconds (protects a public bot)."""

    def __init__(self, owner_id: int, limit: int = 20, window: float = 10.0) -> None:
        self.owner_id = owner_id
        self.limit = limit
        self.window = window
        self.seen: Dict[int, Deque[float]] = defaultdict(deque)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user: Optional[User] = data.get('event_from_user')
        if user is not None and user.id != self.owner_id:
            now = time.monotonic()
            history = self.seen[user.id]
            while history and now - history[0] > self.window:
                history.popleft()
            if len(history) >= self.limit:
                return None
            history.append(now)
        return await handler(event, data)
