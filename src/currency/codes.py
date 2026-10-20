"""Currency codes, symbols and common words people type after an amount."""
from typing import List, Optional, Sequence, Tuple

# ISO 4217 codes accepted in quick entry (the rates source covers far more; these are the usual ones).
CODES = {
    'USD', 'EUR', 'GBP', 'CHF', 'JPY', 'CNY', 'KRW', 'TRY', 'AED', 'INR', 'CAD', 'AUD', 'PLN', 'CZK', 'SEK', 'NOK',
    'RUB', 'BYN', 'UAH', 'KZT', 'UZS', 'KGS', 'TJS', 'AZN', 'AMD', 'GEL', 'MDL', 'TMT', 'THB', 'VND', 'IDR', 'ILS',
}

ALIASES = {
    '$': 'USD', 'долл': 'USD', 'доллар': 'USD', 'доллара': 'USD', 'долларов': 'USD', 'dollar': 'USD', 'dollars': 'USD',
    '€': 'EUR', 'евро': 'EUR', 'euro': 'EUR',
    '£': 'GBP', '¥': 'JPY', '₩': 'KRW', '₺': 'TRY', '₹': 'INR',
    '₽': 'RUB', 'руб': 'RUB', 'рубль': 'RUB', 'рубля': 'RUB', 'рублей': 'RUB', 'р': 'RUB',
    '₸': 'KZT', 'тенге': 'KZT', 'тг': 'KZT',
    'сум': 'UZS', 'сума': 'UZS', 'сумов': 'UZS', "so'm": 'UZS',
    '₴': 'UAH', 'грн': 'UAH', 'гривна': 'UAH', 'гривен': 'UAH',
    '₾': 'GEL', 'лари': 'GEL', '₼': 'AZN', 'манат': 'AZN', '֏': 'AMD', 'драм': 'AMD',
    'бел.руб': 'BYN', 'белруб': 'BYN', 'злотых': 'PLN', 'zł': 'PLN', 'юань': 'CNY', 'юаней': 'CNY',
}


def normalize(token: Optional[str]) -> Optional[str]:
    """ISO code for a token like "usd", "USD", "$", "евро"; None if it is not a currency."""
    if not token:
        return None
    value = token.strip().strip('.,').lower()
    if value.upper() in CODES:
        return value.upper()
    return ALIASES.get(value)


def split_currency(words: Sequence[str]) -> Tuple[Optional[str], List[str]]:
    """("USD", rest) when the first word is a currency, else (None, words)."""
    if words:
        code = normalize(words[0])
        if code:
            return code, list(words[1:])
    return None, list(words)
