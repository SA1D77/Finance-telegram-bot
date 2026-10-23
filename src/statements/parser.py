"""Payment notifications from bank bots (Uzcard / Humo style), pasted or forwarded:

    ➖ 37'000 UZS
    🔹 LLC MADINA QANDOLAT
    💳 561468******6104
    🕘 21:48 23.10.2026
    ✅ Успешно

Several blocks in one message are fine. Pure functions, no database.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from currency.codes import normalize as currency_code

# "➖ 37'000 UZS", "+1 500 000,50 so'm", "- 2’500 сум"
_AMOUNT_RE = re.compile(
    r"^\s*(➖|➕|−|-|\+)\s*(\d[\d'’`\s .,]*\d|\d)\s*([A-Za-zА-Яа-яЁё'’$€₽]{1,8})?\s*$")
_TIME_RE = re.compile(r'(\d{1,2}):(\d{2})(?::\d{2})?\s+(\d{1,2})[./](\d{1,2})[./](\d{4})')
_TIME_RE_DATE_FIRST = re.compile(r'(\d{1,2})[./](\d{1,2})[./](\d{4})\s+(\d{1,2}):(\d{2})')
_EMOJI_PREFIX_RE = re.compile(r'^[^\w\d]+\s*', re.UNICODE)
_FAILED_WORDS = ('❌', 'отклон', 'ошибк', 'неуспеш', 'отмен', 'rad etildi', 'xato', 'declined', 'failed', 'cancel')
_SKIP_LINE_PREFIXES = ('💳', '🗓', '📅')


@dataclass
class Payment:
    income: bool
    amount: Decimal
    currency: Optional[str]
    merchant: Optional[str]
    when: Optional[datetime]  # local time, naive
    failed: bool = False


def _amount(raw: str) -> Optional[Decimal]:
    text = re.sub(r"['’`\s ]", '', raw)
    if ',' in text and '.' in text:
        text = text.replace(',', '')       # 1,500.50
    elif text.count(',') == 1 and len(text.split(',')[1]) <= 2:
        text = text.replace(',', '.')      # 1500,50
    else:
        text = text.replace(',', '')       # 1,500,000
    if text.count('.') > 1:
        text = text.replace('.', '')       # 1.500.000
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value > 0 else None


def _when(line: str) -> Optional[datetime]:
    match = _TIME_RE.search(line)
    try:
        if match:
            hour, minute, day, month, year = (int(x) for x in match.groups())
            return datetime(year, month, day, hour, minute)
        match = _TIME_RE_DATE_FIRST.search(line)
        if match:
            day, month, year, hour, minute = (int(x) for x in match.groups())
            return datetime(year, month, day, hour, minute)
    except ValueError:
        return None
    return None


def parse_statement(text: Optional[str]) -> List[Payment]:
    """Every payment block found in the text, in order (failed ones included, flagged)."""
    payments: List[Payment] = []
    current: Optional[Payment] = None
    for raw_line in (text or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _AMOUNT_RE.match(line)
        if match:
            sign, number, unit = match.groups()
            amount = _amount(number)
            if amount is not None:
                current = Payment(income=sign in ('➕', '+'), amount=amount,
                                  currency=currency_code(unit) if unit else None, merchant=None, when=None)
                payments.append(current)
                continue
        if current is None:
            continue
        lowered = line.lower()
        if any(word in lowered for word in _FAILED_WORDS):
            current.failed = True
            continue
        when = _when(line)
        if when is not None:
            current.when = when
            continue
        if line.startswith(_SKIP_LINE_PREFIXES) or '✅' in line:
            continue
        if current.merchant is None:
            merchant = _EMOJI_PREFIX_RE.sub('', line).strip()
            if merchant and not re.fullmatch(r'[\d*\s]+', merchant):  # not a masked card number
                current.merchant = merchant[:255]
    return payments


def looks_like_statement(text: Optional[str]) -> bool:
    """At least one payment with both an amount line and a date/time line."""
    return any(p.when is not None for p in parse_statement(text))
