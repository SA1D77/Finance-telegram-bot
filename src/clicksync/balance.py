"""The reply of @clickuz to /balance, read without guessing.

Layout (seen in the owner's own inspect output, 17.11.2026; the userbot has not run /balance live yet):

    Общий баланс:
    2'244'986.91 сум

    Ваши карты:
    💳 HAMK***6104 ⭐️
          2'244'986.91 сум

There may be several cards, each a label line with a masked number followed by an amount line. The first
amount is the aggregate over all cards: it is kept apart and never treated as one more card.

Amount rules (explicit, see parse_amount):
* digits may be grouped by spaces, no-break spaces (U+00A0, U+202F), apostrophes (' ’ `) or one kind of
  punctuation mark used only between groups of exactly three digits;
* a decimal part is 1–2 digits after the LAST '.' or ','; when both '.' and ',' occur, the last one is the
  decimal separator and the other must be a grouping mark;
* "1.000" / "1,000" (one separator before exactly three digits) could be one thousand or one: rejected;
* hidden values ("***", "•••", "скрыт") and anything else are rejected, never read as zero.

Only the masked label is kept (a full number would be masked here); the raw message is never stored.
"""
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

from .core import mask_account

CURRENCIES = {
    'сум': 'UZS', 'сўм': 'UZS', "so'm": 'UZS', 'sum': 'UZS', 'uzs': 'UZS',
    'usd': 'USD', '$': 'USD', 'долл': 'USD', 'eur': 'EUR', '€': 'EUR', 'rub': 'RUB', 'руб': 'RUB', '₽': 'RUB',
}
_GROUPING = "'’`   "
_HIDDEN = re.compile(r'[*•●∙·]{2,}|скрыт|hidden', re.IGNORECASE)
_AMOUNT_LINE = re.compile(r'^\s*([+\-−]?[\d' + _GROUPING + r'.,]*\d)\s*([^\d\s]\S*)?\s*$')
_MASKED_CARD = re.compile(r'(\d{0,6})\s*([*•×xX]{2,})\s*(\d{4})\b')
_FULL_CARD = re.compile(r'\b\d{12,19}\b')
_EMOJI = re.compile('[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]')


class BalanceRejected(Exception):
    """The reply cannot be read safely. The message (Russian) never quotes amounts."""


def parse_amount(raw: str) -> Decimal:
    text = (raw or '').strip().replace('−', '-')
    if not text or _HIDDEN.search(text):
        raise BalanceRejected('сумма скрыта')
    sign = ''
    if text[0] in '+-':
        sign, text = ('-' if text[0] == '-' else ''), text[1:].strip()
    text = re.sub('[' + _GROUPING + ']', 'G', text)
    if not re.fullmatch(r'[\dG.,]+', text) or not text[0].isdigit() or not text[-1].isdigit():
        raise BalanceRejected('не число')
    integer, fraction = text, ''
    tail = re.search(r'[.,](\d+)$', text)
    if tail:
        digits, mark, before = tail.group(1), text[tail.start()], text[:tail.start()]
        if len(digits) in (1, 2):
            integer, fraction = before, digits
        elif len(digits) == 3 and mark in before:
            pass    # "1.234.567": the mark only groups thousands
        elif len(digits) == 3:
            raise BalanceRejected('неоднозначная сумма: одна точка или запятая перед тремя цифрами')
        else:
            raise BalanceRejected('больше двух знаков после запятой')
    marks = {ch for ch in integer if not ch.isdigit()}
    if len(marks) > 1:
        raise BalanceRejected('непонятные разделители')
    if marks:
        parts = integer.split(marks.pop())
        if not 1 <= len(parts[0]) <= 3 or any(len(part) != 3 for part in parts[1:]):
            raise BalanceRejected('непонятная группировка цифр')
        integer = ''.join(parts)
    try:
        return Decimal(sign + integer + ('.' + fraction if fraction else ''))
    except InvalidOperation:
        raise BalanceRejected('не число') from None


def currency_of(token: Optional[str]) -> str:
    word = (token or '').strip().casefold().rstrip('.')
    for name, code in CURRENCIES.items():
        if word == name or (len(name) > 2 and word.startswith(name)):
            return code
    raise BalanceRejected('неизвестная валюта' if word else 'не указана валюта')


def _amount_line(line: str) -> Tuple[Decimal, str]:
    if _HIDDEN.search(line):
        raise BalanceRejected('баланс скрыт в Click (кнопка «Скрыть баланс»)')
    match = _AMOUNT_LINE.match(line)
    if not match:
        raise BalanceRejected('строка суммы в непонятном формате')
    return parse_amount(match.group(1)), currency_of(match.group(2))


@dataclass(frozen=True)
class CardBalance:
    label: str          # masked, emoji removed, e.g. "HAMK***6104"
    last4: str
    amount: Decimal
    currency: str


@dataclass
class BalanceReply:
    total: Dict[str, Decimal] = field(default_factory=dict)     # aggregate per currency, never a card
    cards: List[CardBalance] = field(default_factory=list)
    ambiguous_last4: List[str] = field(default_factory=list)    # two cards end in the same four digits


def is_balance_reply(text: Optional[str]) -> bool:
    return 'общий баланс' in (text or '').casefold() and 'ваши карты' in (text or '').casefold()


def _label(line: str) -> Tuple[str, str]:
    clean = ' '.join(_EMOJI.sub(' ', line).split())
    clean = _FULL_CARD.sub(lambda m: mask_account(m.group(0)), clean)
    match = _MASKED_CARD.search(clean)
    if not match:
        raise BalanceRejected('у карты не найден маскированный номер')
    return clean[:32], match.group(3)


def parse_balance_reply(text: Optional[str]) -> BalanceReply:
    """The reply as numbers, or BalanceRejected. Every line must be understood: an unknown line rejects it."""
    if not is_balance_reply(text):
        raise BalanceRejected('это не ответ на /balance')
    lines = [line.strip() for line in (text or '').splitlines()]
    lines = [line for line in lines if line]
    reply = BalanceReply()
    position = 0
    while position < len(lines) and 'общий баланс' not in lines[position].casefold():
        position += 1
    position += 1
    while position < len(lines) and 'ваши карты' not in lines[position].casefold():
        value, currency = _amount_line(lines[position])
        if currency in reply.total:
            raise BalanceRejected('общий баланс указан дважды в одной валюте')
        reply.total[currency] = value
        position += 1
    if not reply.total:
        raise BalanceRejected('нет общего баланса')
    position += 1
    pending_label: Optional[Tuple[str, str]] = None
    for line in lines[position:]:
        if pending_label is None:
            pending_label = _label(line)
            continue
        value, currency = _amount_line(line)
        reply.cards.append(CardBalance(label=pending_label[0], last4=pending_label[1], amount=value, currency=currency))
        pending_label = None
    if pending_label is not None:
        raise BalanceRejected('у карты нет суммы (ответ неполный)')
    if not reply.cards:
        raise BalanceRejected('в ответе нет ни одной карты')
    seen: Dict[Tuple[str, str], int] = {}
    for card in reply.cards:
        seen[(card.last4, card.currency)] = seen.get((card.last4, card.currency), 0) + 1
    reply.ambiguous_last4 = sorted({last4 for (last4, _), count in seen.items() if count > 1})
    if len({(c.label, c.currency) for c in reply.cards}) != len(reply.cards):
        raise BalanceRejected('одна и та же карта указана дважды')
    # The aggregate must be the sum of the cards in its currency: otherwise some card is missing or misread.
    for currency, total in reply.total.items():
        cards = [c.amount for c in reply.cards if c.currency == currency]
        if cards and sum(cards) != total:
            raise BalanceRejected('сумма по картам не совпадает с общим балансом (ответ неполный?)')
    return reply
