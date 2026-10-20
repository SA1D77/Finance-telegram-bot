from aiogram import Dispatcher
from aiogram.fsm.storage.base import BaseStorage

from config import FEATURE_QUICK_INPUT
from i18n import setup_middleware
from middleware.ensure_user import EnsureUserMiddleware
from middleware.owner import OwnerOnlyMiddleware
from telegram import (
    category_handlers,
    common_handlers,
    language_handlers,
    main_handlers,
    report_handlers,
    settings_handlers,
)


def create_dispatcher(storage: BaseStorage, owner_id: int, quick_input: bool = FEATURE_QUICK_INPUT) -> Dispatcher:
    dp = Dispatcher(storage=storage)
    # Runs before i18n and routing: other users are ignored without a reply.
    dp.update.outer_middleware(OwnerOnlyMiddleware(owner_id=owner_id))
    dp.update.outer_middleware(EnsureUserMiddleware())
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
    if quick_input:
        # Last: it only catches plain messages that start with an amount.
        from quick_input.handlers import router as quick_input_router
        dp.include_router(quick_input_router)

    return dp
