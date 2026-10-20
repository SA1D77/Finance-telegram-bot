import asyncio
import logging
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeChat
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from config import TELEGRAM, DEBUG, OWNER_ID, REDIS_HOST, REDIS_PORT, REDIS_PASSWORD
from dispatcher import create_dispatcher

# Redis db for FSM states, separate from the cache (db 3) and Celery (db 0).
FSM_REDIS_DB = 4


COMMANDS = {
    'ru': ['История операций', 'Отменить последнюю операцию', 'Бюджет и лимиты на месяц', 'Аналитика за месяц',
           'Регулярные операции', 'Цели накопления', 'Отчёт за период', 'Экспорт в CSV / XLSX',
           'Написать разработчику: ошибка или идея'],
    'uz': ['Amallar tarixi', "Oxirgi amalni bekor qilish", 'Oylik byudjet va limitlar', 'Oylik tahlil',
           'Muntazam amallar', "Jamg'arma maqsadlari", 'Davr bo\'yicha hisobot', 'CSV / XLSX eksport',
           "Dasturchiga yozish: xato yoki g'oya"],
    'en': ['Operation history', 'Undo the last operation', 'Monthly budget and limits', 'Monthly analytics',
           'Recurring operations', 'Savings goals', 'Report for a period', 'Export to CSV / XLSX',
           'Write to the developer: a bug or an idea'],
    'be': ['Гісторыя аперацый', 'Адмяніць апошнюю аперацыю', 'Бюджэт і ліміты на месяц', 'Аналітыка за месяц',
           'Рэгулярныя аперацыі', 'Мэты назапашвання', 'Справаздача за перыяд', 'Экспарт у CSV / XLSX',
           'Напісаць распрацоўшчыку: памылка ці ідэя'],
}
COMMAND_NAMES = ['history', 'undo', 'budget', 'stats', 'recurring', 'goals', 'report', 'export', 'feedback']


async def set_commands(bot: Bot) -> None:
    """The / menu in each language; the owner also sees /backup."""
    for lang, descriptions in COMMANDS.items():
        commands = [BotCommand(command=name, description=text) for name, text in zip(COMMAND_NAMES, descriptions)]
        await bot.set_my_commands(commands, language_code=lang)
        if lang == 'ru':
            await bot.set_my_commands(commands)  # default for every other language
    owner_commands = [BotCommand(command=name, description=text) for name, text in zip(COMMAND_NAMES, COMMANDS['ru'])]
    owner_commands.append(BotCommand(command='backup', description='Резервная копия сейчас'))
    await bot.set_my_commands(owner_commands, scope=BotCommandScopeChat(chat_id=OWNER_ID))


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

    await set_commands(bot)

    try:
        await dp.start_polling(bot)
    finally:
        await storage.close()
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
