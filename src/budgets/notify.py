import asyncio
import logging

from config import FEATURE_BUDGETS

logger = logging.getLogger(__name__)


async def schedule_budget_check(user_id: int) -> None:
    """Queues a budget check after an operation was saved or changed. Never fails the caller."""
    if not FEATURE_BUDGETS:
        return
    from .tasks import check_budgets
    try:
        # .delay talks to Redis synchronously; keep it off the event loop.
        await asyncio.to_thread(check_budgets.delay, user_id)
    except Exception:
        logger.warning('Could not queue a budget check', exc_info=False)
