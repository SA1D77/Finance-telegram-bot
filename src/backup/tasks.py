"""Celery: database backup on schedule (beat) or on /backup, sent to the owner in Telegram."""
import asyncio
import logging
from typing import Optional

from celery_app import celery

logger = logging.getLogger(__name__)
TELEGRAM_FILE_LIMIT = 49 * 1024 * 1024  # bots can upload up to 50 MB


def _params():
    from config import HOST, PORT, POSTGRES_DB, POSTGRES_PASSWORD, POSTGRES_USER
    from .dump import DbParams
    return DbParams(host=HOST, port=PORT, user=POSTGRES_USER, password=POSTGRES_PASSWORD, database=POSTGRES_DB)


@celery.task(ignore_result=True)
def backup_database(chat_id: Optional[int] = None) -> None:
    asyncio.run(run_backup(chat_id))


async def run_backup(chat_id: Optional[int] = None, bot=None, now=None) -> Optional[str]:
    """Creates a copy, keeps the newest BACKUP_KEEP and sends the file; returns its path."""
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from aiogram.types import FSInputFile

    from config import BACKUP_DIR, BACKUP_KEEP, BACKUP_PG_DUMP, BACKUP_TO_TELEGRAM, OWNER_ID, TELEGRAM
    from timeutils import local_now
    from .dump import create_backup, rotate

    chat_id = chat_id or OWNER_ID
    own_bot = bot is None
    bot = bot or Bot(token=TELEGRAM, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        try:
            path = await asyncio.to_thread(create_backup, _params(), BACKUP_DIR, now or local_now(), BACKUP_PG_DUMP)
        except Exception as error:
            logger.error('Backup failed: %s', type(error).__name__)
            if chat_id:
                await bot.send_message(chat_id=chat_id, text=f'⚠️ Резервная копия не создана ({type(error).__name__}). Подробности: docker compose logs celery')
            return None
        rotate(BACKUP_DIR, BACKUP_KEEP)
        if chat_id and BACKUP_TO_TELEGRAM:
            size = path.stat().st_size
            if size <= TELEGRAM_FILE_LIMIT:
                await bot.send_document(chat_id=chat_id, document=FSInputFile(str(path)),
                                        caption=f'💾 Резервная копия {path.name} ({size // 1024} КБ). Восстановление: restore.bat')
            else:
                await bot.send_message(chat_id=chat_id, text=f'💾 Копия {path.name} слишком большая для Telegram, она лежит в папке backups.')
        return str(path)
    finally:
        if own_bot:
            await bot.session.close()


async def queue_backup(chat_id: int) -> bool:
    """Queues a backup from the bot process; False if the broker is unreachable."""
    try:
        await asyncio.to_thread(backup_database.delay, chat_id)
        return True
    except Exception:
        logger.warning('Could not queue a backup', exc_info=False)
        return False
