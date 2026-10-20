from aiogram import Dispatcher
from aiogram.fsm.storage.base import BaseStorage

from i18n import setup_middleware
from telegram import (
    category_handlers,
    common_handlers,
    language_handlers,
    main_handlers,
    report_handlers,
    settings_handlers,
)


def create_dispatcher(storage: BaseStorage) -> Dispatcher:
    dp = Dispatcher(storage=storage)
    setup_middleware(dp)

    # Same order the handler modules were imported in under aiogram 2,
    # so handler priority does not change.
    dp.include_routers(
        category_handlers.router,
        common_handlers.router,
        language_handlers.router,
        main_handlers.router,
        report_handlers.router,
        settings_handlers.router,
    )

    return dp
