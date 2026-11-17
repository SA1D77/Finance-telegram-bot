"""The userbot process: python -m clicksync.worker (the `clicksync` service in docker compose).

Every TICK seconds it reports that it is alive and decides whether to synchronise: when "sync now" was
pressed in the bot, or when CLICK_SYNC_INTERVAL_MIN passed since the last run (unless paused). Any failure
ends the run with an error recorded in sync_state; the next run starts over from the database, which holds
everything needed to resume (periods not completed are read again).
Notifications go to the owner through the accounting bot (Bot API), never from the owner's account.
"""
import asyncio
import logging
import sys
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

from config import OWNER_ID, TELEGRAM
from . import queries, settings
from .clickuz import ClickAdapter
from .engine import RunResult, SyncError, run_sync
from .texts import discrepancy_text
from timeutils import utc_now

logger = logging.getLogger('clicksync')

TICK = 30           # seconds between checks of sync_state
RETRY_AFTER = 15    # minutes: an automatic run that failed is retried sooner than the full interval


def is_due(state, now: datetime, interval_minutes: int) -> bool:
    if state is None or state.start_date is None:
        return False
    if state.sync_requested:
        return True     # an explicit request works even while automatic runs are paused
    if state.paused:
        return False
    if state.last_started_at is None:
        return True
    failed = state.last_error is not None and (state.last_finished_at or now) >= state.last_started_at
    wait = min(interval_minutes, RETRY_AFTER) if failed else interval_minutes
    return now - state.last_started_at >= timedelta(minutes=wait)


Notifier = Callable[[str, Optional[dict]], Awaitable[None]]


async def notify_findings(user_id: int, notify: Notifier) -> int:
    """Sends every open discrepancy not yet announced, once (notified_at), with a button to open it."""
    sent = []
    for d in await queries.unnotified_discrepancies(user_id):
        period = await queries.get_period(user_id, d.period_id)
        transactions = await queries.period_transactions(user_id, d.period_id) if period is not None else []
        tx = await queries.get_transaction(user_id, d.transaction_id) if d.transaction_id else None
        markup = {'inline_keyboard': [[{'text': 'Открыть вопрос', 'callback_data': f'cs:dv:{d.id}'}]]}
        await notify(discrepancy_text(d, period, transactions, tx), markup)
        sent.append(d.id)
    await queries.mark_notified(user_id, sent)
    return len(sent)


async def run_once(user_id: int, adapter_factory: Callable[[], Awaitable[ClickAdapter]], notify: Notifier,
                   today, user_currency: Optional[str]) -> Optional[RunResult]:
    """One synchronisation with its bookkeeping. Returns None when it failed (the error is stored)."""
    previous = await queries.get_state(user_id)
    await queries.mark_started(user_id)
    pending_before = await queries.pending_count(user_id)
    result, error = None, None
    try:
        adapter = await adapter_factory()
        result = await run_sync(adapter, queries.DbStore(user_id), today, settings.OVERLAP_DAYS)
        await queries.auto_link_exact(user_id, user_currency, settings.to_utc)
        await queries.check_ledger(user_id, user_currency)
    except SyncError as failure:
        error = str(failure)
    except (ConnectionError, OSError, asyncio.TimeoutError) as failure:
        error = f'нет связи с Telegram ({type(failure).__name__}), повторю позже'
    except Exception as failure:     # never let one bad run stop the process
        logger.exception('Sync run failed')
        error = f'внутренняя ошибка: {type(failure).__name__}'
    await queries.mark_finished(user_id, error)

    new = await queries.pending_count(user_id) - pending_before
    if new > 0:
        await notify(f'🧾 Из Click загружено новых платежей: {new}. Разобрать: /click', None)
    if error and (previous is None or previous.last_error != error):   # the same failure is reported once
        await notify(f'⚠️ Синхронизация с Click не удалась: {error}', None)
    await notify_findings(user_id, notify)
    return result


def _bot_notifier() -> Notifier:
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from aiogram.types import InlineKeyboardMarkup

    bot = Bot(token=TELEGRAM, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    async def notify(text: str, markup: Optional[dict]) -> None:
        try:
            await bot.send_message(OWNER_ID, text, reply_markup=InlineKeyboardMarkup.model_validate(markup) if markup else None)
        except Exception as failure:
            logger.warning('Notification not sent: %s', type(failure).__name__)

    return notify


async def _user_currency(user_id: int) -> Optional[str]:
    from config import BASE_CURRENCY
    from middleware.user import get_settings
    _locale, currency = await get_settings(user_id)
    return currency or BASE_CURRENCY or None


async def main() -> int:
    logging.basicConfig(level=logging.INFO)
    if OWNER_ID is None:
        logger.critical('OWNER_ID is not set')
        return 1
    try:
        settings.zone()
        settings.api_credentials()
    except settings.ConfigError as error:
        logger.critical('%s', error)
        return 1
    from .telethon_port import TelethonPort, make_client
    client = make_client()
    notify = _bot_notifier()
    port: Optional[TelethonPort] = None

    async def adapter_factory() -> ClickAdapter:
        nonlocal port
        if not client.is_connected():
            await client.connect()
            port = None
        if not await client.is_user_authorized():
            raise SyncError('Telegram-сессия не авторизована. Выполните вход: python -m clicksync.login (см. CLICK.md).')
        if port is None:
            if (await client.get_me()).id != OWNER_ID:
                raise SyncError('Сессия принадлежит не владельцу бота. Синхронизация остановлена.')
            port = TelethonPort(client, settings.BOT)
            await port.start()
        return ClickAdapter(port, settings.profile(), download_dir=settings.DATA_DIR / 'reports',
                            step_delay=settings.STEP_DELAY, reply_timeout=settings.REPLY_TIMEOUT,
                            file_timeout=settings.FILE_TIMEOUT)

    logger.info('Click sync worker started')
    while True:
        try:
            await queries.ensure_state(OWNER_ID)
            await queries.heartbeat(OWNER_ID)
            state = await queries.get_state(OWNER_ID)
            if is_due(state, utc_now(), settings.INTERVAL_MINUTES):
                today = datetime.now(settings.zone()).date()
                result = await run_once(OWNER_ID, adapter_factory, notify, today, await _user_currency(OWNER_ID))
                if result is not None:
                    logger.info('Sync done: %d periods, %d new, %d updated', len(result.visited), result.inserted, result.updated)
        except Exception as failure:    # database restarting and the like: try again on the next tick
            logger.warning('Worker tick failed: %s', type(failure).__name__)
            try:
                await client.disconnect()
            except Exception:
                pass
        await asyncio.sleep(TICK)


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
