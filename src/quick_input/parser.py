"""Parses quick entries such as "500 кафе обед", "+50000 зарплата", "1200+350*2 продукты"."""
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from accounting.models import OperationType
from telegram.validation import checked_amount, multiplier, normalize_expression
from .calc import CalcError, evaluate, is_expression

# Optional sign, then an expression made of digits, operators and spaces, then the words.
_QUICK_RE = re.compile(r'^\s*([+-]?)\s*([\d(.,][\d\s.,+\-*/()×÷]*)(.*)$', re.DOTALL)


class AmountError(ValueError):
    """The text looks like a quick entry but the amount cannot be used."""


@dataclass
class QuickEntry:
    operation_type: OperationType
    amount: Decimal
    expression: Optional[str]  # set only when the amount was calculated
    words: List[str]


def looks_like_quick_entry(text: Optional[str]) -> bool:
    return bool(text) and _QUICK_RE.match(text) is not None


def parse_quick_entry(text: str) -> QuickEntry:
    """Raises AmountError when the amount part is not a valid positive amount."""
    match = _QUICK_RE.match(text or '')
    if match is None:
        raise AmountError('not a quick entry')
    sign, raw_expression, rest = match.groups()
    expression = normalize_expression(raw_expression.strip())

    try:
        value = evaluate(expression)
    except CalcError as error:
        raise AmountError(str(error)) from error

    words = rest.split()
    if words and multiplier(words[0]):  # "500к кафе", "1.5 млн машина"
        value *= multiplier(words[0])
        words = words[1:]

    amount = checked_amount(value)
    if amount is None:
        raise AmountError('amount out of range')

    return QuickEntry(
        # "+" means income; no sign or "-" means an expense.
        operation_type=OperationType.income if sign == '+' else OperationType.expenditure,
        amount=amount,
        expression=expression if is_expression(expression) else None,
        words=words,
    )
