"""Categories every new user starts with, in their language (name, kind)."""
from typing import Dict, List, Tuple

DEFAULT_CATEGORIES: Dict[str, List[Tuple[str, str]]] = {
    'ru': [
        ('продукты', 'expense'), ('кафе и рестораны', 'expense'), ('транспорт', 'expense'), ('такси', 'expense'),
        ('дом и коммуналка', 'expense'), ('связь и интернет', 'expense'), ('здоровье', 'expense'), ('одежда', 'expense'),
        ('развлечения', 'expense'), ('подарки', 'expense'), ('другое', 'expense'),
        ('зарплата', 'income'), ('подработка', 'income'), ('кэшбэк', 'income'), ('другие доходы', 'income'),
    ],
    'uz': [
        ('oziq-ovqat', 'expense'), ('kafe va restoranlar', 'expense'), ('transport', 'expense'), ('taksi', 'expense'),
        ('uy va kommunal', 'expense'), ('aloqa va internet', 'expense'), ("sog'liq", 'expense'), ('kiyim', 'expense'),
        ("ko'ngilochar", 'expense'), ("sovg'alar", 'expense'), ('boshqa', 'expense'),
        ('maosh', 'income'), ("qo'shimcha ish", 'income'), ('keshbek', 'income'), ('boshqa daromad', 'income'),
    ],
    'en': [
        ('groceries', 'expense'), ('cafes and restaurants', 'expense'), ('transport', 'expense'), ('taxi', 'expense'),
        ('home and utilities', 'expense'), ('phone and internet', 'expense'), ('health', 'expense'), ('clothes', 'expense'),
        ('entertainment', 'expense'), ('gifts', 'expense'), ('other', 'expense'),
        ('salary', 'income'), ('side job', 'income'), ('cashback', 'income'), ('other income', 'income'),
    ],
    'be': [
        ('прадукты', 'expense'), ('кафэ і рэстараны', 'expense'), ('транспарт', 'expense'), ('таксі', 'expense'),
        ('дом і камуналка', 'expense'), ('сувязь і інтэрнэт', 'expense'), ('здароўе', 'expense'), ('адзенне', 'expense'),
        ('забавы', 'expense'), ('падарункі', 'expense'), ('іншае', 'expense'),
        ('заробак', 'income'), ('падпрацоўка', 'income'), ('кэшбэк', 'income'), ('іншыя даходы', 'income'),
    ],
}
