"""Celery side: check budgets after a change and notify through Telegram."""
import asyncio
import logging

from celery_app import celery

logger = logging.getLogger(__name__)


@celery.task(ignore_result=True)
def check_budgets(user_id: int) -> None:
    asyncio.run(check_and_notify(user_id))


async def check_and_notify(user_id: int, bot=None) -> int:
    """Sends one message per budget that crossed a new threshold; returns how many were sent."""
    import database
    from i18n import i18n
    import money
    from middleware.user import get_settings
    from .service import new_alerts
    from .texts import alert_text

    try:
        alerts = await new_alerts(user_id)
        if not alerts:
            return 0
        lang, currency = await get_settings(user_id)
        lang = lang or i18n.default_locale
        money.set_currency(currency)  # amounts in this user's currency
        with i18n.context():
            texts = [alert_text(status, threshold, lambda message: i18n.gettext(message, locale=lang))
                     for status, threshold in alerts]
        return await _send(user_id, texts, bot)
    finally:
        # Each task runs in a fresh event loop: pooled connections from the last one must not be reused.
        await database.engine.dispose()


async def _send(user_id: int, texts, bot=None) -> int:
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from config import TELEGRAM

    own_bot = bot is None
    bot = bot or Bot(token=TELEGRAM, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    sent = 0
    try:
        for text in texts:
            try:
                await bot.send_message(chat_id=user_id, text=text)
                sent += 1
            except Exception:
                # The alert is already recorded, so it will not be retried; amounts stay out of the log.
                logger.warning('Could not deliver a budget alert', exc_info=False)
    finally:
        if own_bot:
            await bot.session.close()
    return sent
