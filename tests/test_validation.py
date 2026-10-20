from decimal import Decimal

import pytest

from telegram.validation import normalize_category_name, parse_amount, parse_category_id


@pytest.mark.parametrize('text, expected', [
    ('100', Decimal('100.00')),
    ('12.5', Decimal('12.50')),
    ('12,5', Decimal('12.50')),
    (' 1 000 ', Decimal('1000.00')),
    ('0.105', Decimal('0.11')),  # rounded half up to cents
    ('999999999999.99', Decimal('999999999999.99')),
])
def test_parse_amount_valid(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize('text', [
    None, '', 'abc', '0', '-5', '0.001', 'nan', 'NaN', 'inf', '-inf', '1e400', '1000000000000', '1.2.3', '12,5,1',
])
def test_parse_amount_invalid(text):
    assert parse_amount(text) is None


def test_parse_amount_has_no_float_noise():
    assert parse_amount('0.1') + parse_amount('0.2') == Decimal('0.30')


@pytest.mark.parametrize('data, expected', [
    ('category_1', 1),
    ('category_12345', 12345),
    ('category_', None),
    ('category_-1', None),
    ('category_Еда', None),
    ('category_1 OR 1=1', None),
    ('cancel', None),
    (None, None),
])
def test_parse_category_id(data, expected):
    assert parse_category_id(data) == expected


@pytest.mark.parametrize('text, expected', [
    ('Еда', 'еда'),
    ('  Кафе   и  рестораны ', 'кафе и рестораны'),
    ('a\nb', 'a b'),
    ('я' * 64, 'я' * 64),
    ('я' * 65, None),
    ('   ', None),
    ('', None),
    (None, None),
])
def test_normalize_category_name(text, expected):
    assert normalize_category_name(text) == expected
