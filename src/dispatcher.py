from aiogram import Dispatcher
from aiogram.fsm.storage.base import BaseStorage

from config import ACCESS_MODE, FEATURE_RECEIPTS, FEATURE_STATEMENTS, FEATURE_VOICE
from config import FEATURE_ANALYTICS, FEATURE_BUDGETS, FEATURE_HISTORY, FEATURE_EXPORT, FEATURE_GOALS, FEATURE_QUICK_INPUT, FEATURE_RECURRING
from i18n import setup_middleware
from middleware.ensure_user import EnsureUserMiddleware
from middleware.menu_reset import MenuResetMiddleware
from middleware.owner import AccessMiddleware, ThrottleMiddleware
from telegram import (
    category_handlers,
    common_handlers,
    language_handlers,
    main_handlers,
    report_handlers,
    settings_handlers,
    fallback_handlers,
)


def create_dispatcher(storage: BaseStorage, owner_id: int, public: bool = ACCESS_MODE == 'public',
                      quick_input: bool = FEATURE_QUICK_INPUT,
                      history: bool = FEATURE_HISTORY, budgets: bool = FEATURE_BUDGETS,
                      analytics: bool = FEATURE_ANALYTICS, recurring: bool = FEATURE_RECURRING,
                      goals: bool = FEATURE_GOALS, export: bool = FEATURE_EXPORT) -> Dispatcher:
    dp = Dispatcher(storage=storage)
    # Runs before i18n and routing: in personal mode other users are ignored without a reply.
    dp.update.outer_middleware(AccessMiddleware(owner_id=owner_id, public=public))
    dp.update.outer_middleware(ThrottleMiddleware(owner_id=owner_id))
    dp.update.outer_middleware(EnsureUserMiddleware())
    setup_middleware(dp)
    dp.message.outer_middleware(MenuResetMiddleware())  # after i18n: compares translated button texts

    # Same order the handler modules were imported in under aiogram 2,
    # so handler priority does not change.
    from onboarding.handlers import router as onboarding_router
    dp.include_router(onboarding_router)  # first: a new user picks language and currency
    dp.include_routers(
        category_handlers.router,
        common_handlers.router,
        language_handlers.router,
        main_handlers.router,
        report_handlers.router,
        settings_handlers.router,
    )
    from feedback.errors import on_error
    from feedback.handlers import router as feedback_router
    dp.include_router(feedback_router)
    dp.errors.register(on_error)  # unexpected errors: apology to the user, notice to the owner
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
    if export:
        from export.handlers import router as export_router
        dp.include_router(export_router)
    if FEATURE_STATEMENTS:
        # Before quick entry: a forwarded bank notification with several payments is not one entry.
        from statements.handlers import router as statements_router
        dp.include_router(statements_router)
    if FEATURE_VOICE or FEATURE_RECEIPTS:
        from media.handlers import router as media_router
        dp.include_router(media_router)
    if quick_input:
        # Last: it only catches plain messages that start with an amount.
        from quick_input.handlers import router as quick_input_router
        dp.include_router(quick_input_router)

    # Very last: answers anything nothing else handled.
    dp.include_router(fallback_handlers.router)

    return dp
