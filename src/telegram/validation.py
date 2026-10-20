import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from accounting.models import CATEGORY_NAME_MAX_LENGTH, COMMENT_MAX_LENGTH
from quick_input.calc import CalcError, evaluate

CENT = Decimal('0.01')
MAX_AMOUNT = Decimal('999999999999.99')  # numeric(14, 2)

# "1 000" or "1 000 000,50": spaces as thousands separators, not two numbers.
_GROUPED_NUMBER_RE = re.compile(r'^\d{1,3}(?: \d{3})+(?:[.,]\d+)?$')


def checked_amount(value: Decimal) -> Optional[Decimal]:
    """Rounds to cents; None unless 0 < value <= MAX_AMOUNT."""
    # Range check before quantize: quantizing a huge value like 1e400 raises.
    if not value.is_finite() or value <= 0 or value > MAX_AMOUNT:
        return None
    value = value.quantize(CENT, rounding=ROUND_HALF_UP)
    if value <= 0:
        return None
    return value


def normalize_expression(text: str) -> str:
    text = ' '.join(text.split())
    if _GROUPED_NUMBER_RE.match(text):
        return text.replace(' ', '')
    return text


def parse_amount(text: Optional[str]) -> Optional[Decimal]:
    """A positive amount: a number ("12,5", "1 000") or a calculation ("1200+350*2"). None if invalid."""
    if not text:
        return None
    try:
        value = evaluate(normalize_expression(text))
    except CalcError:
        return None
    return checked_amount(value)


def parse_category_id(callback_data: Optional[str], prefix: str = 'category_') -> Optional[int]:
    """`category_<id>` -> id. Anything else (including old name-based buttons) -> None."""
    if not callback_data or not callback_data.startswith(prefix):
        return None
    raw = callback_data[len(prefix):]
    return int(raw) if raw.isdigit() else None


def normalize_category_name(text: Optional[str]) -> Optional[str]:
    """Lower-cased single-line name of 1..64 characters, or None."""
    if not text:
        return None
    name = ' '.join(text.split()).lower()
    if not name or len(name) > CATEGORY_NAME_MAX_LENGTH:
        return None
    return name


def normalize_comment(words) -> Optional[str]:
    """Free text after the category, single line, at most 255 characters."""
    comment = ' '.join(' '.join(words).split())
    return comment[:COMMENT_MAX_LENGTH] or None
