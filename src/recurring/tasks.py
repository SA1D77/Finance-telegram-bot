"""Celery beat task: create due recurring operations and send reminders."""
import asyncio
import logging
from typing import List, Optional, Tuple

from celery_app import celery

logger = logging.getLogger(__name__)


@celery.task(ignore_result=True)
def process_recurring() -> None:
    from config import FEATURE_RECURRING
    if FEATURE_RECURRING:
        asyncio.run(run(bot=None))


async def build_messages(today) -> List[Tuple[int, str, Optional[object]]]:
    """(chat id, text, markup) for every created operation and every reminder due today."""
    from aiogram import types

    from config import FEATURE_HISTORY
    from i18n import i18n
    import money
    from middleware.user import get_settings
    from . import service
    from .texts import summary

    messages = []
    langs = {}

    async def lang_of(user_id):
        """The user's language; also switches amounts to the user's currency."""
        if user_id not in langs:
            lang, currency = await get_settings(user_id)
            langs[user_id] = (lang or i18n.default_locale, currency)
        lang, currency = langs[user_id]
        money.set_currency(currency)
        return lang

    for item in await service.run_due(today):
        lang = await lang_of(item.template.user_id)
        with i18n.context():
            gettext = lambda message: i18n.gettext(message, locale=lang)  # noqa: E731
            text = gettext('🔁 Добавлено по расписанию ({date}):').format(date=item.day.strftime('%d.%m.%Y'))
            text += '\n' + summary(item.template, gettext)
            markup = None
            if FEATURE_HISTORY:
                markup = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(
                    text=gettext('↩️ Отменить'), callback_data=f'h:undo:{item.operation_id.hex}')]])
        messages.append((item.template.user_id, text, markup))

    for row in await service.reminders_due(today):
        lang = await lang_of(row.user_id)
        with i18n.context():
            gettext = lambda message: i18n.gettext(message, locale=lang)  # noqa: E731
            text = gettext('⏰ {date} по расписанию будет добавлено:').format(date=row.next_date.strftime('%d.%m.%Y'))
            text += '\n' + summary(row, gettext)
            markup = types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(text=gettext('⏭ Пропустить'), callback_data=f'r:skip:{row.id}'),
                types.InlineKeyboardButton(text=gettext('💰 Сумма'), callback_data=f'r:amt:{row.id}'),
            ]])
        messages.append((row.user_id, text, markup))
    return messages


async def run(bot=None, today=None) -> int:
    import database
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from config import FEATURE_BUDGETS, TELEGRAM
    from timeutils import local_now

    today = today or local_now().date()
    own_bot = bot is None
    try:
        messages = await build_messages(today)
        if not messages:
            return 0
        bot = bot or Bot(token=TELEGRAM, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        sent = 0
        for chat_id, text, markup in messages:
            try:
                await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
                sent += 1
            except Exception:
                logger.warning('Could not deliver a recurring operation message', exc_info=False)
        if FEATURE_BUDGETS:
            from budgets.tasks import check_and_notify
            for user_id in {chat_id for chat_id, _, _ in messages}:
                await check_and_notify(user_id, bot=bot)
        return sent
    finally:
        if own_bot and bot is not None:
            await bot.session.close()
        await database.engine.dispose()
