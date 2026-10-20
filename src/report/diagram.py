from decimal import Decimal
from typing import List
from dataclasses import dataclass
from collections import defaultdict

# Figure API instead of pyplot: no shared global state between reports built in threads.
from matplotlib.figure import Figure

from accounting.models import OperationType
from .headers import INCOME, EXPENDITURE


@dataclass
class MoneyMovement():
    operation: str
    quantity: Decimal


def create_diagram(files, accounts, lang):
    if not lang:
        lang = 'ru'
    income_data, expenditure_data = prepare_data(accounts=accounts)

    income_categories = [i for i in income_data.keys()]
    income_values = [i.quantity for i in income_data.values()]

    expenditure_categories = [i for i in expenditure_data.keys()]
    expenditure_values = [i.quantity for i in expenditure_data.values()]

    drow_diagram(path=files.income_diagram,
                 values=income_values,
                 categories=income_categories)

    drow_diagram(path=files.expenditure_diagram,
                 values=expenditure_values,
                 categories=expenditure_categories)

    drow_diagram(path=files.general_diagram,
                 values=[sum(income_values), sum(expenditure_values)],
                 categories=[INCOME[lang], EXPENDITURE[lang]])


def drow_diagram(path: str, values: List[float], categories: List[str]):
    values = [float(value) for value in values]  # matplotlib cannot draw Decimal; fine for a picture
    figure = Figure()
    axes = figure.subplots()
    # An empty period still produces a (blank) image, as before.
    if any(values):
        axes.pie(values, labels=categories, autopct='%1.1f%%', startangle=90)
        axes.axis('equal')
    else:
        axes.axis('off')

    figure.savefig(path)


def prepare_data(accounts):
    income_data = defaultdict(lambda: MoneyMovement(operation='income', quantity=Decimal(0)))
    expenditure_data = defaultdict(lambda: MoneyMovement(operation='expenditure', quantity=Decimal(0)))

    for i in accounts:
        if i['operation_type'] == OperationType.income.value:
            income_data[i['category']].quantity += Decimal(i['quantity'])
        else:
            expenditure_data[i['category']].quantity += Decimal(i['quantity'])

    return income_data, expenditure_data
