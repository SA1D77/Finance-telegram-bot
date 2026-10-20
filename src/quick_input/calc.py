"""Safe arithmetic for amounts: + - * / and parentheses on Decimal.

A tiny tokenizer plus a recursive descent parser; no eval/exec, no names,
no powers. Grammar:

    expr   := term (('+' | '-') term)*
    term   := factor (('*' | '/') factor)*
    factor := ('+' | '-') factor | NUMBER | '(' expr ')'
"""
import re
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext
from typing import List, Tuple

MAX_LENGTH = 100
MAX_DEPTH = 20
MAX_TOKENS = 60

# "×" and "÷" come from phone keyboards; "," is a decimal comma. Letters such as
# "x"/"х" are deliberately not operators: "100 хлеб" must not become a product.
_OPERATOR_ALIASES = {'×': '*', '÷': '/'}
_TOKEN_RE = re.compile(r'\s*(?:(\d+(?:[.,]\d+)?|[.,]\d+)|(.))')


class CalcError(ValueError):
    pass


def _tokenize(text: str) -> List[Tuple[str, str]]:
    tokens: List[Tuple[str, str]] = []
    for number, symbol in _TOKEN_RE.findall(text):
        if number:
            tokens.append(('num', number.replace(',', '.')))
        elif symbol.isspace() or symbol == '':
            continue
        else:
            symbol = _OPERATOR_ALIASES.get(symbol, symbol)
            if symbol not in '+-*/()':
                raise CalcError(f'unexpected character {symbol!r}')
            tokens.append(('op', symbol))
    if len(tokens) > MAX_TOKENS:
        raise CalcError('expression is too long')
    return tokens


class _Parser:
    def __init__(self, tokens: List[Tuple[str, str]]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.depth = 0

    def _peek(self) -> Tuple[str, str]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else ('end', '')

    def _take(self) -> Tuple[str, str]:
        token = self._peek()
        self.pos += 1
        return token

    def parse(self) -> Decimal:
        value = self._expr()
        if self._peek()[0] != 'end':
            raise CalcError('unexpected token')
        return value

    def _expr(self) -> Decimal:
        value = self._term()
        while self._peek() in (('op', '+'), ('op', '-')):
            op = self._take()[1]
            right = self._term()
            value = value + right if op == '+' else value - right
        return value

    def _term(self) -> Decimal:
        value = self._factor()
        while self._peek() in (('op', '*'), ('op', '/')):
            op = self._take()[1]
            right = self._factor()
            if op == '*':
                value = value * right
            else:
                if right == 0:
                    raise CalcError('division by zero')
                value = value / right
        return value

    def _factor(self) -> Decimal:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise CalcError('too deeply nested')
        try:
            kind, text = self._take()
            if (kind, text) == ('op', '-'):
                return -self._factor()
            if (kind, text) == ('op', '+'):
                return self._factor()
            if kind == 'num':
                return Decimal(text)
            if (kind, text) == ('op', '('):
                value = self._expr()
                if self._take() != ('op', ')'):
                    raise CalcError('missing )')
                return value
            raise CalcError('number expected')
        finally:
            self.depth -= 1


def evaluate(text: str) -> Decimal:
    """Value of an arithmetic expression. Raises CalcError for anything else."""
    if not text or len(text) > MAX_LENGTH:
        raise CalcError('empty or too long')
    tokens = _tokenize(text)
    if not tokens:
        raise CalcError('empty')
    with localcontext() as ctx:
        ctx.prec = 34
        ctx.traps[DivisionByZero] = True
        ctx.traps[InvalidOperation] = True
        try:
            return _Parser(tokens).parse()
        except (DivisionByZero, InvalidOperation) as error:
            raise CalcError(str(error)) from error


def is_expression(text: str) -> bool:
    """True if the text is a calculation rather than a single number."""
    try:
        tokens = _tokenize(text)
    except CalcError:
        return False
    return any(kind == 'op' for kind, _ in tokens)
