"""Budget wording shared by /budget and the Celery notifications."""
from html import escape
from typing import Callable

from money import fmt
from .service import BudgetStatus

BAR_WIDTH = 10


def bar(percent: int) -> str:
    filled = min(BAR_WIDTH, max(0, round(percent * BAR_WIDTH / 100)))
    return '▓' * filled + '░' * (BAR_WIDTH - filled)


def status_emoji(percent: int) -> str:
    return '🔴' if percent >= 100 else '🟡' if percent >= 80 else '🟢'


def budget_name(status: BudgetStatus, gettext: Callable[[str], str]) -> str:
    return gettext('Общий бюджет') if status.category_id is None else escape(status.name.capitalize())


def alert_text(status: BudgetStatus, threshold: int, gettext: Callable[[str], str]) -> str:
    name = budget_name(status, gettext)
    if threshold >= 100:
        return gettext('🔴 {name}: лимит исчерпан. Потрачено {spent} из {limit} ({percent}%), перерасход {over}.').format(
            name=name, spent=fmt(status.spent), limit=fmt(status.limit), percent=status.percent, over=fmt(-status.left))
    return gettext('🟡 {name}: потрачено {spent} из {limit} ({percent}%). Осталось {left}.').format(
        name=name, spent=fmt(status.spent), limit=fmt(status.limit), percent=status.percent, left=fmt(status.left))
