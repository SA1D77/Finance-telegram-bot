"""Russian texts of the card balance checks. Pure functions: the bot and the userbot share them."""
from datetime import datetime
from decimal import Decimal
from html import escape
from typing import Any, Callable, Dict, Iterable, List, Optional

from money import number

UNITS = {'UZS': 'сум'}
STATUS = {
    'ok': '✅ сходится',
    'explained': '✅ объяснено',
    'waiting': '⏳ жду отчёт',
    'unexplained': '❓ не объяснено',
    'uncertain': '⚠️ не могу сверить',
}
KINDS = {
    'expense': 'расход',
    'income': 'доход',
    'own_transfer': 'перевод между своими',
    'fee': 'комиссия',
    'refund': 'возврат',
    'adjustment': 'корректировка',
}


def money(value: Optional[Decimal], currency: Optional[str]) -> str:
    if value is None:
        return '—'
    unit = UNITS.get(currency or '', currency or '')
    return f'{number(value)} {unit}'.strip()


def card_name(last4: Optional[str], label: str = '') -> str:
    """Masked: only the last four digits are ever shown."""
    return f'••••{last4}' if last4 else escape(label or 'карта')


def span(start: datetime, end: datetime) -> str:
    """The actual interval in local time; one day is written once. Never "today": a span may be days long."""
    if start.date() == end.date():
        return f'{start:%d.%m.%Y} с {start:%H:%M} до {end:%H:%M}'
    return f'с {start:%d.%m.%Y %H:%M} до {end:%d.%m.%Y %H:%M}'


def question(interval: Any, local: Callable[[datetime], datetime]) -> str:
    """The question to the owner about the unexplained part, in neutral words."""
    remaining = interval.remaining or Decimal(0)
    who = card_name(interval.last4, interval.card)
    when = span(local(interval.start_at), local(interval.end_at))
    size = money(abs(remaining), interval.currency)
    if remaining < 0:
        return (f'По карте {who} {when} осталось на {size} меньше, чем получается по операциям. '
                'На что ушли деньги?')
    return (f'По карте {who} {when} на {size} больше, чем получается по операциям. '
            'Откуда пришли деньги?')


def interval_text(interval: Any, local: Callable[[datetime], datetime], explanations: Iterable[Any] = ()) -> str:
    lines = [f'💳 <b>Карта {card_name(interval.last4, interval.card)}</b> · {STATUS.get(interval.status, interval.status)}',
             f'Промежуток: {span(local(interval.start_at), local(interval.end_at))}',
             f'Баланс в начале: {money(interval.opening, interval.currency)}']
    if interval.expected is not None:
        if interval.incoming:
            lines.append(f'Поступления по отчёту: +{money(interval.incoming, interval.currency)}')
        if interval.outgoing:
            lines.append(f'Списания по отчёту: −{money(interval.outgoing, interval.currency)}')
        if interval.fees:
            lines.append(f'Комиссии сверх сумм: −{money(interval.fees, interval.currency)}')
        lines.append(f'Должно быть по операциям: {money(interval.expected, interval.currency)}')
    lines.append(f'Баланс в конце: {money(interval.closing, interval.currency)}')
    explanations = list(explanations)
    if explanations:
        lines.append('Ваши объяснения:')
        for e in explanations:
            sign = '+' if e.direction == 'in' else '−'
            lines.append(f'• {KINDS.get(e.kind, e.kind)}: {sign}{money(e.amount, e.currency)}')
    if interval.remaining:
        lines.append(f'Не объяснено: <b>{"+" if interval.remaining > 0 else "−"}'
                     f'{money(abs(interval.remaining), interval.currency)}</b>')
    if interval.reason:
        lines.append(('⚠️ ' if interval.status == 'uncertain' else 'ℹ️ ') + escape(interval.reason))
    if interval.unresolved:
        lines.append('🕓 Отмечено «Не помню»: больше не спрашиваю, но промежуток остаётся открытым.')
    if interval.status == 'unexplained' and not interval.unresolved:
        lines.append('')
        lines.append(question(interval, local))
    return '\n'.join(lines)


def review_text(asked: List[Any], others: List[Any], local: Callable[[datetime], datetime]) -> str:
    """The daily review: what needs an answer, grouped by card, and what cannot be judged yet."""
    lines = ['⚖️ <b>Сверка балансов карт</b>']
    by_card: Dict[str, List[Any]] = {}
    for interval in asked:
        by_card.setdefault(card_name(interval.last4, interval.card), []).append(interval)
    for name, items in by_card.items():
        lines.append('')
        lines.append(f'💳 {name}:')
        for i in items:
            sign = '+' if i.remaining > 0 else '−'
            lines.append(f'• {span(local(i.start_at), local(i.end_at))}: не объяснено {sign}{money(abs(i.remaining), i.currency)}')
    if others:
        uncertain = sum(1 for i in others if i.status == 'uncertain')
        waiting = sum(1 for i in others if i.status == 'waiting')
        later = len(others) - uncertain - waiting
        parts = [f'не могу сверить: {uncertain}' if uncertain else '', f'жду отчёт: {waiting}' if waiting else '',
                 f'отложено или «Не помню»: {later}' if later else '']
        lines.append('')
        lines.append('Другие промежутки — ' + ', '.join(p for p in parts if p) + '.')
    if not asked:
        lines.append('')
        lines.append('Вопросов нет.')
    return '\n'.join(lines)


def balances_text(observed_at: Optional[datetime], snapshots: Iterable[Any], local: Callable[[datetime], datetime]) -> str:
    if observed_at is None:
        return '💳 Баланс карт ещё не проверялся.'
    lines = [f'💳 <b>Баланс на {local(observed_at):%d.%m.%Y %H:%M}</b>']
    for s in snapshots:
        if s.kind == 'total':
            lines.append(f'Всего: {money(s.amount, s.currency)}')
        else:
            mark = ' ⚠️ (окончание номера неоднозначно)' if s.ambiguous or not s.last4 else ''
            lines.append(f'• {card_name(s.last4, s.card)}: {money(s.amount, s.currency)}{mark}')
    return '\n'.join(lines)


def confirm_text(interval: Any, kind: str, direction: str, value: Decimal, category: Optional[str]) -> str:
    sign = '+' if direction == 'in' else '−'
    lines = ['Подтвердите:',
             f'Карта {card_name(interval.last4, interval.card)}, {KINDS.get(kind, kind)}: <b>{sign}{money(value, interval.currency)}</b>']
    if category:
        lines.append(f'Категория: {escape(category.capitalize())}')
        lines.append('В учёт будет добавлена операция.')
    else:
        lines.append('В учёт ничего не записывается: разница только помечается объяснённой.')
    return '\n'.join(lines)
