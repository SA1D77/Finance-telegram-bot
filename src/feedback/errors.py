"""Unexpected errors: the user gets a short apology, the owner a notice (type and place only, no user data)."""
import logging
import time
import traceback
from typing import Dict

from aiogram import Bot
from aiogram.types import ErrorEvent

from config import OWNER_ID

logger = logging.getLogger(__name__)

REPEAT_SECONDS = 600
_last_report: Dict[str, float] = {}


def _place(error: BaseException) -> str:
    """file:line of the deepest frame inside the project (not in site-packages)."""
    frames = [f for f in traceback.extract_tb(error.__traceback__) if 'site-packages' not in f.filename]
    if not frames:
        return '?'
    frame = frames[-1]
    path = frame.filename.replace('\\', '/')
    short = path.split('/src/', 1)[-1] if '/src/' in path else path.rsplit('/', 1)[-1]
    return f'{short}:{frame.lineno} in {frame.name}'


def _kind(event: ErrorEvent) -> str:
    update = event.update
    if update.message:
        return 'message'
    if update.callback_query:
        return 'button ' + (update.callback_query.data or '').split(':', 1)[0][:20]
    return 'update'


def _user(event: ErrorEvent):
    update = event.update
    source = update.message or update.callback_query
    return source.from_user if source and source.from_user else None


async def on_error(event: ErrorEvent, bot: Bot) -> bool:
    error = event.exception
    logger.error('Unhandled error: %s', type(error).__name__, exc_info=error)
    place = _place(error)
    key = f'{type(error).__name__}@{place}'
    now = time.monotonic()

    user = _user(event)
    user_id = user.id if user else None
    if user is not None:
        try:
            from i18n import i18n
            # The request's i18n context is already gone here, so pick the language explicitly.
            with i18n.context():
                text = i18n.gettext('⚠️ Что-то пошло не так. Разработчик уже получил отчёт, попробуйте ещё раз чуть позже.',
                                    locale=user.language_code or i18n.default_locale)
            await bot.send_message(chat_id=user_id, text=text)
        except Exception:
            pass

    if OWNER_ID and now - _last_report.get(key, -REPEAT_SECONDS) >= REPEAT_SECONDS:
        _last_report[key] = now
        try:
            # Deliberately without the message text or amounts: only what is needed to find the bug.
            await bot.send_message(chat_id=OWNER_ID, text=(
                f'🐞 Ошибка: <code>{type(error).__name__}</code>\n📍 <code>{place}</code>\n🔘 {_kind(event)}\n'
                f'👤 id <code>{user_id}</code>\nПодробности: docker compose logs --tail 100 app'))
        except Exception:
            logger.warning('Could not notify the owner about an error', exc_info=False)
    return True
