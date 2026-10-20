import os
from datetime import datetime
from typing import Any, AsyncGenerator, List, Optional

# Config is read at import time, so test values must be set before any project import.
os.environ.setdefault('TELEGRAM', '123456:TEST-TOKEN')
os.environ.setdefault('DEBUG', '0')
os.environ.setdefault('DB_URL', 'postgresql+asyncpg://test:test@localhost:5432/test')
os.environ.setdefault('REDIS_HOST', 'localhost')
os.environ.setdefault('REDIS_PORT', '6379')
os.environ.setdefault('CELERY_HOST', 'localhost')
os.environ.setdefault('CELERY_PORT', '6379')

import pytest  # noqa: E402
from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.methods import AnswerCallbackQuery, TelegramMethod  # noqa: E402
from aiogram.types import CallbackQuery, Chat, Message, Update, User  # noqa: E402

from dispatcher import create_dispatcher  # noqa: E402

USER_ID = 1001
USERNAME = 'owner'


class MockedSession(BaseSession):
    """Records every Bot API call instead of sending it to Telegram."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: List[TelegramMethod[Any]] = []

    async def close(self) -> None:
        pass

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: Optional[int] = None) -> Any:
        self.requests.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        return Message(message_id=len(self.requests), date=datetime.now(), chat=Chat(id=USER_ID, type='private'),
                       text=getattr(method, 'text', None))

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        yield b''

    def texts(self) -> List[str]:
        return [m.text for m in self.requests if getattr(m, 'text', None) is not None]


def make_user(language_code: str = 'ru', username: Optional[str] = USERNAME) -> User:
    return User(id=USER_ID, is_bot=False, first_name='Test', username=username, language_code=language_code)


def make_message(text: str, user: Optional[User] = None) -> Update:
    return Update(update_id=1, message=Message(
        message_id=1, date=datetime.now(), chat=Chat(id=USER_ID, type='private'),
        from_user=user or make_user(), text=text,
    ))


def make_callback(data: str, user: Optional[User] = None) -> Update:
    user = user or make_user()
    return Update(update_id=2, callback_query=CallbackQuery(
        id='cb', from_user=user, chat_instance='ci', data=data,
        message=Message(message_id=2, date=datetime.now(), chat=Chat(id=USER_ID, type='private'), from_user=user, text='x'),
    ))


_storage = MemoryStorage()
# Routers are module-level and can be attached to only one Dispatcher, so build it once.
_dp = create_dispatcher(storage=_storage)


class BotHarness:
    def __init__(self) -> None:
        self.session = MockedSession()
        self.bot = Bot(token=os.environ['TELEGRAM'], session=self.session)
        self.storage = _storage
        self.storage.storage.clear()
        self.dp = _dp
        self.key = StorageKey(bot_id=self.bot.id, chat_id=USER_ID, user_id=USER_ID)

    async def feed(self, update: Update) -> None:
        await self.dp.feed_update(self.bot, update)

    async def state(self) -> Optional[str]:
        return await self.storage.get_state(self.key)

    async def set_state(self, state: Any) -> None:
        await self.storage.set_state(self.key, state)

    async def data(self) -> dict:
        return await self.storage.get_data(self.key)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> BotHarness:
    import i18n

    async def no_saved_lang(username: str) -> None:
        return None

    # Locale stored in Redis; tests that need a saved language override this.
    monkeypatch.setattr(i18n, '_get_user_lang', no_saved_lang)
    return BotHarness()
