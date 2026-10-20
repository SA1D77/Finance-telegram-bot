"""Russian number words to digits, for speech recognition output.

"пятьсот кафе" -> "500 кафе", "плюс пятьдесят тысяч зарплата" -> "+50000 зарплата",
"две тысячи триста сорок" -> "2340", "полторы тысячи" -> "1500", "5 тысяч" -> "5000".
"""
from decimal import Decimal
from typing import List, Optional

UNITS = {
    'ноль': 0, 'один': 1, 'одна': 1, 'одну': 1, 'одно': 1, 'два': 2, 'две': 2, 'три': 3, 'четыре': 4, 'пять': 5,
    'шесть': 6, 'семь': 7, 'восемь': 8, 'девять': 9, 'десять': 10, 'одиннадцать': 11, 'двенадцать': 12,
    'тринадцать': 13, 'четырнадцать': 14, 'пятнадцать': 15, 'шестнадцать': 16, 'семнадцать': 17,
    'восемнадцать': 18, 'девятнадцать': 19,
}
TENS = {
    'двадцать': 20, 'тридцать': 30, 'сорок': 40, 'пятьдесят': 50, 'шестьдесят': 60, 'семьдесят': 70,
    'восемьдесят': 80, 'девяносто': 90,
}
HUNDREDS = {
    'сто': 100, 'двести': 200, 'триста': 300, 'четыреста': 400, 'пятьсот': 500, 'шестьсот': 600,
    'семьсот': 700, 'восемьсот': 800, 'девятьсот': 900,
}
SCALES = {
    'тысяча': 1000, 'тысячи': 1000, 'тысяч': 1000, 'тысячу': 1000, 'тыщ': 1000, 'тыщи': 1000, 'тыща': 1000,
    'миллион': 10 ** 6, 'миллиона': 10 ** 6, 'миллионов': 10 ** 6, 'лям': 10 ** 6, 'ляма': 10 ** 6,
}
HALF = {'полтора': Decimal('1.5'), 'полторы': Decimal('1.5')}
SIGNS = {'плюс': '+', 'минус': '-'}


def _small(word: str) -> Optional[int]:
    for table in (UNITS, TENS, HUNDREDS):
        if word in table:
            return table[word]
    return None


class _Number:
    def __init__(self) -> None:
        self.total = Decimal(0)
        self.group = Decimal(0)
        self.started = False

    def add(self, word: str) -> bool:
        """Consumes the word if it continues the number."""
        if word.isdigit() and not self.started:
            self.group, self.started = Decimal(word), True
            return True
        value = _small(word)
        if value is not None:
            self.group += value
            self.started = True
            return True
        if word in HALF:
            self.group += HALF[word]
            self.started = True
            return True
        if word in SCALES and self.started:
            self.total += (self.group or 1) * SCALES[word]
            self.group = Decimal(0)
            return True
        if word in SCALES:  # "тысяча рублей" without a number before it
            self.total += SCALES[word]
            self.started = True
            return True
        return False

    def value(self) -> str:
        result = self.total + self.group
        return str(result.to_integral_value()) if result == result.to_integral_value() else str(result)


def words_to_digits(text: str) -> str:
    tokens: List[str] = []
    number: Optional[_Number] = None
    pending_sign = ''
    for word in text.lower().split():
        if number is not None and number.add(word):
            continue
        if number is not None:
            tokens.append(pending_sign + number.value())
            number, pending_sign = None, ''
        if word in SIGNS and not tokens:
            pending_sign = SIGNS[word]
            continue
        candidate = _Number()
        if candidate.add(word):
            number = candidate
            continue
        if pending_sign:
            tokens.append(pending_sign)
            pending_sign = ''
        tokens.append(word)
    if number is not None:
        tokens.append(pending_sign + number.value())
    elif pending_sign:
        tokens.append(pending_sign)
    return ' '.join(tokens)
