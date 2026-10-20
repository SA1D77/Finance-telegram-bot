from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, TelegramObject

from i18n import _

# Texts of the reply-keyboard menu buttons (source strings, translated at runtime).
MENU_KEYS = (
    '➕ Доход', '➖ Расход', '🗂 Категории', '📥 Добавить категорию', '📤 Удалить категорию', '📊 Баланс',
    '⚙️ Настройки', '🕒 Последние 10 операций', '📜 История', '🎯 Бюджет', '📈 Аналитика', '🔁 Регулярные', '🐷 Цели',
)


class MenuResetMiddleware(BaseMiddleware):
    """A menu button or a /command always works: it leaves whatever dialog was half-finished.

    Without this, pressing "📊 Баланс" while the bot waited for a category button did nothing at all.
    Registered on messages after the i18n middleware, so button texts compare in the user's language.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        state: FSMContext = data.get('state')
        text = event.text if isinstance(event, Message) else None
        if state is not None and text and await state.get_state() is not None:
            if text.startswith('/') or text in {_(key) for key in MENU_KEYS}:
                await state.clear()
                data['raw_state'] = None  # state filters read this value, computed before us
        return await handler(event, data)
