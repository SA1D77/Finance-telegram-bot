from decimal import Decimal
from types import SimpleNamespace

import pytest

from accounting.models import OperationType
from quick_input.matching import match_category
from quick_input.parser import AmountError, looks_like_quick_entry, parse_quick_entry
from telegram.validation import normalize_comment

EXP, INC = OperationType.expenditure, OperationType.income


@pytest.mark.parametrize('text, op, amount, expression, words', [
    ('500 кафе обед', EXP, '500.00', None, ['кафе', 'обед']),
    ('+50000 зарплата', INC, '50000.00', None, ['зарплата']),
    ('-300 такси', EXP, '300.00', None, ['такси']),
    ('1200+350*2 продукты', EXP, '1900.00', '1200+350*2', ['продукты']),
    ('1200 + 350 * 2 продукты', EXP, '1900.00', '1200 + 350 * 2', ['продукты']),
    ('45.5/3 кафе', EXP, '15.17', '45.5/3', ['кафе']),
    ('+(1000-200)*3 премия', INC, '2400.00', '(1000-200)*3', ['премия']),
    ('1 000 такси', EXP, '1000.00', None, ['такси']),
    ('12,5 кофе', EXP, '12.50', None, ['кофе']),
    ('500', EXP, '500.00', None, []),
    ('500кафе', EXP, '500.00', None, ['кафе']),
])
def test_parse_quick_entry(text, op, amount, expression, words):
    entry = parse_quick_entry(text)

    assert (entry.operation_type, entry.amount, entry.expression, entry.words) == (op, Decimal(amount), expression, words)


@pytest.mark.parametrize('text', ['0 кафе', '5/0 кафе', '500 2 кафе', '1+ кафе', '10000000000000 кафе', '0.001 кафе'])
def test_parse_quick_entry_rejects_bad_amounts(text):
    with pytest.raises(AmountError):
        parse_quick_entry(text)


@pytest.mark.parametrize('text, expected', [
    ('500 кафе', True), ('+5 x', True), ('(1+2) y', True), ('.5 кофе', True),
    ('кафе 500', False), ('привет', False), ('', False), (None, False), ('➕ Доход', False),
])
def test_looks_like_quick_entry(text, expected):
    assert looks_like_quick_entry(text) is expected


CATEGORIES = [SimpleNamespace(id=i, name=n) for i, n in enumerate(
    ['кафе', 'продукты', 'кафе и рестораны', 'зарплата', 'транспорт', 'такси', 'ёлка', 'подарки', 'подписки'], start=1)]


def names(match):
    return [c.name for c in match.categories]


@pytest.mark.parametrize('words, kind, found, rest', [
    (['кафе', 'обед'], 'exact', ['кафе'], ['обед']),
    (['Кафе', 'и', 'рестораны', 'ужин'], 'exact', ['кафе и рестораны'], ['ужин']),   # longest exact wins
    (['ЗАРПЛАТА'], 'exact', ['зарплата'], []),
    (['елка'], 'exact', ['ёлка'], []),                                               # ё == е
    (['прод', 'хлеб'], 'fuzzy', ['продукты'], ['хлеб']),                             # prefix
    (['кафэ'], 'fuzzy', ['кафе'], []),                                               # typo
    (['зарпалта', 'премия'], 'fuzzy', ['зарплата'], ['премия']),
    (['под'], 'ambiguous', ['подарки', 'подписки'], []),
    (['одежда', 'футболка'], 'none', [], ['футболка']),
    ([], 'empty', [], []),
])
def test_match_category(words, kind, found, rest):
    match = match_category(words, CATEGORIES)

    assert match.kind == kind
    assert sorted(names(match)) == sorted(found)
    assert match.rest == rest


def test_match_without_categories():
    assert match_category(['кафе'], []).kind == 'none'


def test_comment_is_single_line_and_limited():
    assert normalize_comment(['обед', 'с', '\n', 'коллегами']) == 'обед с коллегами'
    assert normalize_comment([]) is None
    assert len(normalize_comment(['я' * 300])) == 255
