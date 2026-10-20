import os
import shutil
import uuid
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional

from accounting.models import OperationType
from analytics.charts import draw_monthly, months_between
from timeutils import format_local, to_local
from .diagram import create_diagram
from .headers import EXPENDITURE, INCOME
from .table import create_report

GENERATED_DIR = os.path.join(os.path.dirname(__file__), 'generated')


@dataclass
class ReportFiles:
    directory: str
    table: str
    general_diagram: str
    expenditure_diagram: str
    income_diagram: str
    dynamics_diagram: Optional[str] = None  # only when the rows span two or more months


def serialize_accounts(accounts: Iterable[Any]) -> List[Dict[str, Any]]:
    """Plain JSON-safe rows, so Celery does not need pickle."""
    return [
        {
            'category': account.categories,
            'date': format_local(account.created_at),
            'month': to_local(account.created_at).strftime('%Y-%m'),
            'quantity': str(account.quantity),  # Decimal as text: exact and JSON-safe
            'operation_type': account.operation_type.value,
            'comment': getattr(account, 'comment', None) or '',
        }
        for account in accounts
    ]


def build_report_files(accounts: List[Dict[str, Any]], lang: str) -> ReportFiles:
    """Writes the table and diagrams into a fresh uuid-named directory."""
    directory = os.path.join(GENERATED_DIR, uuid.uuid4().hex)
    os.makedirs(directory)

    files = ReportFiles(
        directory=directory,
        table=os.path.join(directory, 'report.xlsx'),
        general_diagram=os.path.join(directory, 'general_diagram.png'),
        expenditure_diagram=os.path.join(directory, 'expenditure_diagram.png'),
        income_diagram=os.path.join(directory, 'income_diagram.png'),
    )
    try:
        create_report(path=files.table, accounts=accounts, lang=lang)
        create_diagram(files=files, accounts=accounts, lang=lang)
        _create_dynamics(files, accounts, lang)
    except Exception:
        remove_report_files(files)
        raise
    return files


def _create_dynamics(files: ReportFiles, accounts: List[Dict[str, Any]], lang: str) -> None:
    totals: Dict[tuple, List[Decimal]] = defaultdict(lambda: [Decimal('0'), Decimal('0')])
    for row in accounts:
        year, month = (int(part) for part in row['month'].split('-'))
        totals[(year, month)][0 if row['operation_type'] == OperationType.income.value else 1] += Decimal(row['quantity'])
    if len(totals) < 2:
        return
    files.dynamics_diagram = os.path.join(files.directory, 'dynamics_diagram.png')
    draw_monthly(files.dynamics_diagram, months_between(min(totals), max(totals)),
                 {key: tuple(value) for key, value in totals.items()}, INCOME[lang], EXPENDITURE[lang])


def remove_report_files(files: ReportFiles) -> None:
    shutil.rmtree(files.directory, ignore_errors=True)
