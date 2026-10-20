from aiogram import Dispatcher
from aiogram.fsm.storage.base import BaseStorage

from config import FEATURE_ANALYTICS, FEATURE_BUDGETS, FEATURE_HISTORY, FEATURE_GOALS, FEATURE_QUICK_INPUT, FEATURE_RECURRING
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


def create_dispatcher(storage: BaseStorage, owner_id: int, quick_input: bool = FEATURE_QUICK_INPUT,
                      history: bool = FEATURE_HISTORY, budgets: bool = FEATURE_BUDGETS,
                      analytics: bool = FEATURE_ANALYTICS, recurring: bool = FEATURE_RECURRING,
                      goals: bool = FEATURE_GOALS) -> Dispatcher:
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
    if history:
        from history.handlers import router as history_router
        dp.include_router(history_router)
    if budgets:
        from budgets.handlers import router as budgets_router
        dp.include_router(budgets_router)
    if analytics:
        from analytics.handlers import router as analytics_router
        dp.include_router(analytics_router)
    if recurring:
        from recurring.handlers import router as recurring_router
        dp.include_router(recurring_router)
    if goals:
        from goals.handlers import router as goals_router
        dp.include_router(goals_router)
    if quick_input:
        # Last: it only catches plain messages that start with an amount.
        from quick_input.handlers import router as quick_input_router
        dp.include_router(quick_input_router)

    return dp
