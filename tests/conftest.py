import os
from datetime import datetime
from typing import Any, AsyncGenerator, List, Optional, Tuple

# Tests must not depend on the real src/.env of this machine (its currency, mode, passwords...).
import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *args, **kwargs: False

# Config is read at import time, so test values must be set before any project import.
os.environ.setdefault('TELEGRAM', '123456:TEST-TOKEN')
os.environ.setdefault('DEBUG', '0')
# Port 1: nothing listens there, so a test that forgets to stub the database fails fast
# instead of reaching a real server on this machine.
os.environ.setdefault('DB_URL', 'postgresql+asyncpg://test:test@127.0.0.1:1/test')
os.environ.setdefault('REDIS_HOST', 'localhost')
os.environ.setdefault('REDIS_PORT', '6379')
os.environ.setdefault('CELERY_HOST', 'localhost')
os.environ.setdefault('CELERY_PORT', '6379')
os.environ.setdefault('OWNER_ID', '1001')
# A non-UTC zone, so tests really exercise local/UTC conversion (UTC+5, no DST).
os.environ.setdefault('TIMEZONE', 'Asia/Tashkent')
# The media router is registered only when these are on; the recognisers themselves are stubbed.
os.environ.setdefault('FEATURE_VOICE', '1')
os.environ.setdefault('FEATURE_RECEIPTS', '1')

import pytest  # noqa: E402
from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.methods import AnswerCallbackQuery, TelegramMethod  # noqa: E402
from aiogram.types import CallbackQuery, Chat, FSInputFile, Message, Update, User  # noqa: E402

from dispatcher import create_dispatcher  # noqa: E402
from middleware.ensure_user import EnsureUserMiddleware  # noqa: E402

USER_ID = 1001
USERNAME = 'owner'


class MockedSession(BaseSession):
    """Records every Bot API call instead of sending it to Telegram."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: List[TelegramMethod[Any]] = []
        self.sent_files: List[Tuple[str, bool]] = []

    async def close(self) -> None:
        pass

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: Optional[int] = None) -> Any:
        self.requests.append(method)
        for attr in ('document', 'photo'):
            file = getattr(method, attr, None)
            if isinstance(file, FSInputFile):
                # Remember whether the file was on disk at send time (it is deleted afterwards).
                self.sent_files.append((str(file.path), os.path.exists(file.path)))
        if isinstance(method, AnswerCallbackQuery):
            return True
        return Message(message_id=len(self.requests), date=datetime.now(), chat=Chat(id=USER_ID, type='private'),
                       text=getattr(method, 'text', None))

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        yield b''

    def texts(self) -> List[str]:
        return [m.text for m in self.requests if getattr(m, 'text', None) is not None]


def make_user(language_code: str = 'ru', username: Optional[str] = USERNAME, user_id: int = USER_ID) -> User:
    return User(id=user_id, is_bot=False, first_name='Test', username=username, language_code=language_code)


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
_dp = create_dispatcher(storage=_storage, owner_id=USER_ID)
_ensure_user = next(m for m in _dp.update.outer_middleware if isinstance(m, EnsureUserMiddleware))


class BotHarness:
    def __init__(self) -> None:
        self.session = MockedSession()
        self.bot = Bot(token=os.environ['TELEGRAM'], session=self.session)
        self.storage = _storage
        self.storage.storage.clear()
        _ensure_user.known.clear()
        self.dp = _dp
        self.key = StorageKey(bot_id=self.bot.id, chat_id=USER_ID, user_id=USER_ID)
        # What the user chose at onboarding; '' currency = already onboarded, amounts without a symbol.
        self.settings = {'locale': None, 'currency': ''}

    async def feed(self, update: Update) -> None:
        await self.dp.feed_update(self.bot, update)

    async def state(self) -> Optional[str]:
        return await self.storage.get_state(self.key)

    async def set_state(self, state: Any) -> None:
        await self.storage.set_state(self.key, state)

    async def data(self) -> dict:
        return await self.storage.get_data(self.key)


@pytest.fixture(autouse=True)
def budget_checks(monkeypatch: pytest.MonkeyPatch) -> List[int]:
    """Records queued budget checks instead of talking to the Celery broker."""
    from budgets.tasks import check_budgets

    queued: List[int] = []
    monkeypatch.setattr(check_budgets, 'delay', queued.append)
    return queued


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> BotHarness:
    import i18n
    import middleware.ensure_user

    async def no_saved_lang(user_id: int) -> None:
        return None

    async def existing_user(user_id: int, username: Optional[str]) -> bool:
        return False

    harness = BotHarness()

    async def settings(user_id: int):
        return harness.settings['locale'], harness.settings['currency']

    # All read the database; tests that need other answers override them or change harness.settings.
    monkeypatch.setattr(i18n, '_get_user_lang', no_saved_lang)
    monkeypatch.setattr(middleware.ensure_user, 'ensure_user', existing_user)
    monkeypatch.setattr(middleware.ensure_user, 'get_settings', settings)
    return harness
