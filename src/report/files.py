import os
import shutil
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from .diagram import create_diagram
from .table import create_report

GENERATED_DIR = os.path.join(os.path.dirname(__file__), 'generated')


@dataclass
class ReportFiles:
    directory: str
    table: str
    general_diagram: str
    expenditure_diagram: str
    income_diagram: str


def serialize_accounts(accounts: Iterable[Any]) -> List[Dict[str, Any]]:
    """Plain JSON-safe rows, so Celery does not need pickle."""
    return [
        {
            'category': account.categories,
            'date': str(account.created_at),
            'quantity': str(account.quantity),  # Decimal as text: exact and JSON-safe
            'operation_type': account.operation_type.value,
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
    except Exception:
        remove_report_files(files)
        raise
    return files


def remove_report_files(files: ReportFiles) -> None:
    shutil.rmtree(files.directory, ignore_errors=True)
