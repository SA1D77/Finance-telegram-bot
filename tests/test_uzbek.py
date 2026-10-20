import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from babel.messages.mofile import read_mo

from accounting.models import OperationType
from export.exporters import rows_for_export, to_csv
from i18n import i18n
from middleware.menu_reset import MENU_KEYS
from report import files as report_files
from telegram.messages import FLOAT_NUMBER_ERROR, HELP_MESSAGE, QUICK_INPUT_HELP, START_MESSAGE

ROOT = Path(__file__).resolve().parent.parent
CALL_RE = re.compile(r"""(?<![\w.])(?:_|__|gettext)\(\s*'((?:[^'\\]|\\.)+)'""")


def _uz_catalog():
    with open(ROOT / 'locales' / 'uz' / 'LC_MESSAGES' / 'finance.mo', 'rb') as mo:
        return {m.id: m.string for m in read_mo(mo) if m.id}


def test_every_string_in_the_code_has_an_uzbek_translation():
    catalog = _uz_catalog()
    used = set()
    for path in (ROOT / 'src').rglob('*.py'):
        for literal in CALL_RE.findall(path.read_text(encoding='utf-8')):
            used.add(literal.encode().decode('unicode_escape').encode('latin-1').decode('utf-8'))
    missing = sorted(text for text in used if not catalog.get(text))
    assert missing == []


def test_long_texts_are_translated():
    catalog = _uz_catalog()
    for text in (START_MESSAGE, HELP_MESSAGE, FLOAT_NUMBER_ERROR, QUICK_INPUT_HELP):
        assert catalog.get(text)


def test_menu_in_uzbek():
    with i18n.context():
        labels = [i18n.gettext(key, locale='uz') for key in MENU_KEYS]
    assert '➕ Daromad' in labels and '⭐ Maqsadlar' in labels
    assert len(set(labels)) == len(labels)  # menu buttons must stay distinguishable


def test_uzbek_report_and_export(tmp_path, monkeypatch):
    monkeypatch.setattr(report_files, 'GENERATED_DIR', str(tmp_path))
    rows = report_files.serialize_accounts([SimpleNamespace(
        categories='taksi', created_at=datetime(2026, 10, 1, 7, 0), quantity=Decimal('25000.00'),
        operation_type=OperationType.expenditure, comment=None)])

    for lang in ('uz', 'xx', None):
        report_files.remove_report_files(report_files.build_report_files(rows, lang))

    exported = rows_for_export([SimpleNamespace(created_at=datetime(2026, 10, 1, 7, 0), operation_type=OperationType.income,
                                                categories='maosh', quantity=Decimal('5000000'), comment=None)], 'uz')
    assert to_csv(exported, 'uz').decode('utf-8-sig').startswith('Sana;Turi;Kategoriya;Summa')
