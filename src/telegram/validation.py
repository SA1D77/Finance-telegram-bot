from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional

from accounting.models import CATEGORY_NAME_MAX_LENGTH

CENT = Decimal('0.01')
MAX_AMOUNT = Decimal('999999999999.99')  # numeric(14, 2)


def parse_amount(text: Optional[str]) -> Optional[Decimal]:
    """A positive amount with at most cents; accepts "12.5" and "12,5". None if invalid."""
    if not text:
        return None
    try:
        value = Decimal(text.strip().replace(' ', '').replace(',', '.'))
    except InvalidOperation:
        return None
    # Range check before quantize: quantizing a huge value like 1e400 raises.
    if not value.is_finite() or value <= 0 or value > MAX_AMOUNT:
        return None
    value = value.quantize(CENT, rounding=ROUND_HALF_UP)
    if value <= 0 or value > MAX_AMOUNT:
        return None
    return value


def parse_category_id(callback_data: Optional[str]) -> Optional[int]:
    """`category_<id>` -> id. Anything else (including old name-based buttons) -> None."""
    if not callback_data or not callback_data.startswith('category_'):
        return None
    raw = callback_data[len('category_'):]
    return int(raw) if raw.isdigit() else None


def normalize_category_name(text: Optional[str]) -> Optional[str]:
    """Lower-cased single-line name of 1..64 characters, or None."""
    if not text:
        return None
    name = ' '.join(text.split()).lower()
    if not name or len(name) > CATEGORY_NAME_MAX_LENGTH:
        return None
    return name
