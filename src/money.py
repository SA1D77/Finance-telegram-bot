"""Amounts as people read them: "1 500 000 so'm", "$1 250.50", "-300 ₽".

The currency of the user being served is kept in a context variable, set once per update by
UserSettingsMiddleware (and per user in Celery tasks), so every text helper can format without
passing it around.
"""
from contextvars import ContextVar
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import config

_currency: ContextVar[Optional[str]] = ContextVar('currency', default=None)

CURRENCIES = ('UZS', 'RUB', 'BYN', 'KZT', 'USD', 'EUR')
DEFAULT_BY_LANGUAGE = {'uz': 'UZS', 'ru': 'RUB', 'be': 'BYN', 'en': 'USD'}
PREFIX = {'USD': '$', 'EUR': '€', 'GBP': '£'}
SUFFIX = {
    'UZS': {'uz': "so'm", 'en': 'UZS', None: 'сум'},
    'RUB': {None: '₽'},
    'BYN': {'en': 'BYN', None: 'Br'},
    'KZT': {None: '₸'},
    'UAH': {None: '₴'},
}
FLAGS = {'UZS': '🇺🇿', 'RUB': '🇷🇺', 'BYN': '🇧🇾', 'KZT': '🇰🇿', 'USD': '🇺🇸', 'EUR': '🇪🇺'}


def set_currency(code: Optional[str]) -> None:
    _currency.set(code)


def current_currency() -> Optional[str]:
    return _currency.get() or config.BASE_CURRENCY or None


def _locale() -> Optional[str]:
    try:
        from i18n import i18n
        return i18n.current_locale
    except LookupError:  # outside a request (Celery sets the locale explicitly)
        return None


def symbol(code: str, locale: Optional[str] = None) -> str:
    variants = SUFFIX.get(code)
    if not variants:
        return PREFIX.get(code, code)
    locale = locale or _locale()
    return variants.get(locale, variants[None])


def number(amount) -> str:
    """1500000 -> "1 500 000", 1250.5 -> "1 250.50" (cents only when there are any)."""
    value = Decimal(amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    text = f'{abs(value):,.2f}'.replace(',', ' ')
    if text.endswith('.00'):
        text = text[:-3]
    return ('-' if value < 0 else '') + text


def fmt(amount, currency: Optional[str] = '', signed: bool = False) -> str:
    """Formatted amount with the currency; currency='' means the current user's, None means none."""
    code = current_currency() if currency == '' else currency
    value = Decimal(amount)
    sign = '+' if signed and value > 0 else ''
    body = number(value)
    if not code:
        return sign + body
    if code in PREFIX:
        minus = '-' if body.startswith('-') else ''
        return f'{sign}{minus}{PREFIX[code]}{body.lstrip("-")}'
    return f'{sign}{body} {symbol(code)}'
