import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from config import TELEGRAM, DEBUG, REDIS_HOST, REDIS_PORT
from dispatcher import create_dispatcher

# Redis db for FSM states, separate from the cache (db 3) and Celery (db 0).
FSM_REDIS_DB = 4


async def main() -> None:
    if DEBUG:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    bot = Bot(token=TELEGRAM, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    storage = RedisStorage(redis=Redis(host=REDIS_HOST, port=REDIS_PORT, db=FSM_REDIS_DB))
    dp = create_dispatcher(storage=storage)

    try:
        await dp.start_polling(bot)
    finally:
        await storage.close()
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
