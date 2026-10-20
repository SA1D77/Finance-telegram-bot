"""Monthly income/expense bars (matplotlib Figure API: no global pyplot state)."""
from decimal import Decimal
from typing import Dict, List, Sequence, Tuple

from matplotlib.figure import Figure

INCOME_COLOR = '#2e9d5b'
EXPENSE_COLOR = '#d9534f'


def month_labels(months: Sequence[Tuple[int, int]]) -> List[str]:
    return [f'{month:02d}.{year % 100:02d}' for year, month in months]


def draw_monthly(path: str, months: Sequence[Tuple[int, int]], totals: Dict[Tuple[int, int], Tuple[Decimal, Decimal]],
                 income_label: str, expense_label: str) -> None:
    """Grouped bars for every month in `months` (missing months are drawn as zero)."""
    incomes = [float(totals.get(m, (0, 0))[0]) for m in months]
    expenses = [float(totals.get(m, (0, 0))[1]) for m in months]
    positions = range(len(months))
    width = 0.4

    figure = Figure(figsize=(max(6.0, len(months) * 0.7), 4.0))
    axes = figure.subplots()
    axes.bar([p - width / 2 for p in positions], incomes, width, label=income_label, color=INCOME_COLOR)
    axes.bar([p + width / 2 for p in positions], expenses, width, label=expense_label, color=EXPENSE_COLOR)
    axes.set_xticks(list(positions))
    axes.set_xticklabels(month_labels(months), rotation=45 if len(months) > 8 else 0)
    axes.yaxis.grid(True, alpha=0.3)
    axes.set_axisbelow(True)
    axes.legend()
    figure.tight_layout()
    figure.savefig(path)


def months_between(first: Tuple[int, int], last: Tuple[int, int]) -> List[Tuple[int, int]]:
    """Every (year, month) from first to last inclusive."""
    months = []
    year, month = first
    while (year, month) <= last:
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months
