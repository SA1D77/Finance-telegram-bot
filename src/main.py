import asyncio
import logging
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from config import TELEGRAM, DEBUG, OWNER_ID, REDIS_HOST, REDIS_PORT, REDIS_PASSWORD
from dispatcher import create_dispatcher

# Redis db for FSM states, separate from the cache (db 3) and Celery (db 0).
FSM_REDIS_DB = 4


async def main() -> None:
    if DEBUG:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if OWNER_ID is None:
        # Fail closed: without an owner the bot would be open to everyone.
        logging.critical('OWNER_ID is not set or is not a number, see src/.env.example')
        sys.exit(1)

    bot = Bot(token=TELEGRAM, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    storage = RedisStorage(redis=Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, db=FSM_REDIS_DB))
    dp = create_dispatcher(storage=storage, owner_id=OWNER_ID)

    await bot.set_my_commands([
        BotCommand(command='history', description='История операций'),
        BotCommand(command='undo', description='Отменить последнюю операцию'),
        BotCommand(command='budget', description='Бюджет и лимиты на месяц'),
        BotCommand(command='report', description='Отчёт'),
    ])

    try:
        await dp.start_polling(bot)
    finally:
        await storage.close()
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
