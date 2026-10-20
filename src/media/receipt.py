"""Finding the total in OCR text of a receipt."""
import re
from decimal import Decimal, InvalidOperation
from typing import List, Optional

# Words printed next to the total on Russian, Uzbek, Kazakh, Belarusian and English receipts.
TOTAL_WORDS = ('итого', 'итог', 'к оплате', 'всего', 'сумма', 'total', 'jami', 'umumiy', 'барлығы', 'разам', 'усяго')
# 1 234,56 / 1234.56 / 12 345 / 12345 (OCR often puts spaces in thousands).
AMOUNT_RE = re.compile(r'(?<![\d.,])(\d{1,3}(?:[  ]\d{3})+|\d+)(?:[.,](\d{2}))?(?![\d])')
MAX_TOTAL = Decimal('100000000')


def _amounts(line: str) -> List[Decimal]:
    values = []
    for integer, cents in AMOUNT_RE.findall(line):
        try:
            value = Decimal(integer.replace(' ', '').replace(' ', '') + ('.' + cents if cents else ''))
        except InvalidOperation:
            continue
        if 0 < value < MAX_TOTAL:
            values.append(value)
    return values


def extract_total(text: str) -> Optional[Decimal]:
    """The amount on the last line that mentions a total word; otherwise the largest amount with cents."""
    lines = [line.strip().lower() for line in (text or '').splitlines() if line.strip()]
    for line in reversed(lines):
        if any(word in line for word in TOTAL_WORDS):
            amounts = _amounts(line)
            if amounts:
                return amounts[-1].quantize(Decimal('0.01'))
    with_cents = [value for line in lines for value in _amounts(line) if value != value.to_integral_value()]
    return max(with_cents).quantize(Decimal('0.01')) if with_cents else None
