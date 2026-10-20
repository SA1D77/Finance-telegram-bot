CATEGORY = {'ru': 'Категория', 'en': 'Category', 'be': 'Катэгорыя', 'uz': 'Kategoriya'}

DATE = {'ru': 'Дата', 'en': 'Date', 'be': 'Дата', 'uz': 'Sana'}

QUANTITY = {'ru': 'Сумма', 'en': 'Amount', 'be': 'Сума', 'uz': 'Summa'}

OPERATION = {'ru': 'Операция', 'en': 'Operation', 'be': 'Аперацыя', 'uz': 'Amal'}

INCOME = {'ru': 'Доход', 'en': 'Income', 'be': 'Даход', 'uz': 'Daromad'}

EXPENDITURE = {'ru': 'Расход', 'en': 'Expenditure', 'be': 'Расход', 'uz': 'Xarajat'}

COMMENT = {'ru': 'Комментарий', 'en': 'Comment', 'be': 'Каментарый', 'uz': 'Izoh'}


def lang_or_default(lang):
    """Report language: the user's if the headers know it, Russian otherwise."""
    return lang if lang in CATEGORY else 'ru'
