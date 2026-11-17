"""The downloadable report file of @clickuz.

Its format (PDF, XLSX, CSV...) and columns are not known yet: no real file has been inspected. Until one
has, the file is only saved for the owner and nothing is read from it; transactions come from the report
message. Add the parser here once a sample exists and return clicksync.core.ParsedTx rows.
"""
from pathlib import Path
from typing import List

from .core import ParsedTx


class UnknownReportFormat(Exception):
    pass


def parse_report_file(path: Path) -> List[ParsedTx]:
    raise UnknownReportFormat(path.suffix)
