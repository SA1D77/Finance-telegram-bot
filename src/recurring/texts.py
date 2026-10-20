from html import escape
from typing import Any, Callable

from accounting.models import OperationType
from money import fmt

Gettext = Callable[[str], str]


def frequency_label(frequency: str, anchor_day: int, gettext: Gettext) -> str:
    if frequency == 'daily':
        return gettext('каждый день')
    if frequency == 'weekly':
        return gettext('каждую неделю')
    return gettext('каждый месяц, {day}-го').format(day=anchor_day)


def summary(row: Any, gettext: Gettext) -> str:
    label = gettext('ДОХОД') if row.operation_type == OperationType.income else gettext('РАСХОД')
    text = f'{label} <b>{fmt(row.amount)} {escape(row.category)}</b>'
    if row.comment:
        text += f'\n💬 {escape(row.comment)}'
    return text


def card(row: Any, gettext: Gettext) -> str:
    text = summary(row, gettext)
    text += '\n🔁 ' + frequency_label(row.frequency, row.anchor_day, gettext)
    text += '\n📅 ' + gettext('Следующая: {date}').format(date=row.next_date.strftime('%d.%m.%Y'))
    if row.remind_days:
        text += '\n⏰ ' + gettext('Напомнить за {days} дн.').format(days=row.remind_days)
    return text
