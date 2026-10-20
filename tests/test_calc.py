from decimal import Decimal

import pytest

from quick_input.calc import CalcError, evaluate, is_expression


@pytest.mark.parametrize('text, expected', [
    ('500', '500'),
    ('1200+350*2', '1900'),
    ('45.5/3', '15.16666666666666666666666666666667'),
    ('45,5/3', '15.16666666666666666666666666666667'),
    ('(100+50)*2', '300'),
    ('2*(3+(4-1))', '12'),
    (' 10 - 2 - 3 ', '5'),     # left associative
    ('100/4/5', '5'),
    ('-5+10', '5'),
    ('--5', '5'),
    ('+7', '7'),
    ('.5+.5', '1.0'),
    ('3×4', '12'),
    ('10÷4', '2.5'),
    ('0.1+0.2', '0.3'),        # exact, no float noise
])
def test_evaluate(text, expected):
    assert evaluate(text) == Decimal(expected)


@pytest.mark.parametrize('text', [
    '', '   ', '()', '1+', '*2', '1//2', '2**10', '1 2', '(1+2', '1+2)', '5/0', '5/(2-2)',
    '__import__("os").system("x")', 'os', '1e5', '0x10', '1_000', 'abc', '2^3', '10%', '1;2',
    '1' * 101,                 # too long
    '(' * 30 + '1' + ')' * 30,  # too deep
    '+'.join(['1'] * 40),      # too many tokens
])
def test_evaluate_rejects(text):
    with pytest.raises(CalcError):
        evaluate(text)


def test_is_expression():
    assert is_expression('1200+350*2')
    assert is_expression('(5)')
    assert not is_expression('1200')
    assert not is_expression('12,5')
    assert not is_expression('abc')
