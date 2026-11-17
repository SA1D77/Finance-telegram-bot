"""One synchronisation run: walks the report periods back in time and stores what they contain.

Works against two small interfaces, so the whole run is tested without Telegram or PostgreSQL:
- adapter: `open()` -> newest PeriodReport, `older()` -> the previous period or None at the end of history;
- store: the database operations listed in `Store`.
"""
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Protocol, Sequence, Set, Tuple

from .core import IngestPlan, ParsedTx, PeriodReport, StoredTx, dedup_keys, fingerprint, plan_ingest, sync_target
from .reconcile import balance_check

logger = logging.getLogger(__name__)

MAX_STEPS = 400     # periods per run; far more than years of weekly reports


class SyncError(Exception):
    """Stops the run. The message is shown to the user, so it is in Russian and carries no amounts."""


class NotConfigured(SyncError):
    pass


@dataclass
class Finding:
    """A question for the user, stored as a discrepancy (the fingerprint prevents repeats)."""
    kind: str
    fingerprint: str
    details: str
    account: Optional[str] = None
    currency: Optional[str] = None
    transaction_id: Optional[int] = None
    expected: Optional[Decimal] = None
    reported: Optional[Decimal] = None
    difference: Optional[Decimal] = None


@dataclass
class RunResult:
    periods_ok: int = 0
    periods_incomplete: int = 0
    periods_failed: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_before_start: int = 0
    end_of_history: bool = False    # the source has nothing earlier
    reached_target: bool = False
    visited: List[Tuple[date, date]] = field(default_factory=list)


class Adapter(Protocol):
    async def open(self) -> PeriodReport:
        ...

    async def older(self) -> Optional[PeriodReport]:
        ...


class Store(Protocol):
    async def start_date(self) -> Optional[date]:
        ...

    async def history_complete(self) -> bool:
        ...

    async def recover_interrupted(self) -> int:
        ...

    async def unfinished_starts(self) -> List[date]:
        ...

    async def begin_period(self, start: date, end: date) -> int:
        ...

    async def existing(self, keys: Sequence[str]) -> Dict[str, StoredTx]:
        ...

    async def lookalikes(self, transactions: Sequence[ParsedTx]) -> List[StoredTx]:
        """Stored rows of the same cards in the same minutes as these transactions."""

    async def apply(self, period_id: int, report: PeriodReport, plan: IngestPlan, status: str,
                    note: Optional[str], findings: List[Finding]) -> None:
        ...

    async def fail_period(self, period_id: int, note: str, finding: Finding) -> None:
        ...

    async def raise_finding(self, finding: Finding) -> None:
        ...

    async def set_history_complete(self) -> None:
        ...


def _period_label(report: PeriodReport) -> str:
    return f'{report.start:%d.%m.%Y}–{report.end:%d.%m.%Y}'


def _findings(report: PeriodReport, plan: IngestPlan) -> List[Finding]:
    findings: List[Finding] = []
    label = _period_label(report)
    for update in plan.updates:
        if not update.needs_review:
            continue
        changed = ', '.join(sorted(update.changes))
        findings.append(Finding(
            kind='changed_after_delivery', transaction_id=update.stored.id,
            fingerprint=fingerprint('changed', update.stored.id, sorted((k, str(v[1])) for k, v in update.changes.items())),
            details=f'Операция уже записана в учёт, но в отчёте за {label} у неё изменилось: {changed}.'))

    if report.opening_balance is not None or report.closing_balance is not None:
        account = report.balance_account
        currency = report.balance_currency
        scoped = [tx for tx in report.transactions
                  if account in (None, tx.account) and currency in (None, tx.currency)]
        result = balance_check(report.opening_balance, report.closing_balance, scoped)
        if result.status in ('mismatch', 'indeterminate') and result.difference and not result.waiting:
            findings.append(Finding(
                kind='balance_mismatch', account=account, currency=report.balance_currency,
                expected=result.expected, reported=result.reported, difference=result.difference,
                fingerprint=fingerprint('balance', report.start, report.end, account, report.balance_currency,
                                        result.expected, result.reported),
                details=f'Период {label}: баланс не сходится.' + (f' Причина неясна: {result.reason}.' if result.reason else '')))
    if not report.complete:
        findings.append(Finding(
            kind='period_incomplete', fingerprint=fingerprint('incomplete', report.start, report.end, report.note),
            details=f'Период {label} прочитан не полностью: {report.note or "причина неизвестна"}.'))
    return findings


async def _process(store: Store, report: PeriodReport, start_date: date, result: RunResult) -> None:
    period_id = await store.begin_period(report.start, report.end)
    try:
        wanted = [tx for tx in report.transactions if tx.occurred_at is None or tx.occurred_at.date() >= start_date]
        result.skipped_before_start += len(report.transactions) - len(wanted)
        scoped = PeriodReport(**{**report.__dict__, 'transactions': wanted})
        keys = dedup_keys(wanted)
        existing = await store.existing([key for key, _ in keys])
        unmatched = [tx for tx, (key, _) in zip(wanted, keys) if key not in existing]
        lookalikes = await store.lookalikes(unmatched) if unmatched else []
        plan = plan_ingest(existing, wanted, keys, lookalikes)
        status = 'completed' if report.complete else 'incomplete'
        await store.apply(period_id, scoped, plan, status, report.note, _findings(scoped, plan))
    except SyncError:
        raise
    except Exception as error:
        # The type only: an exception message may quote amounts or names from the report.
        note = f'ошибка обработки: {type(error).__name__}'
        logger.error('Period %s failed: %s', _period_label(report), type(error).__name__)
        await store.fail_period(period_id, note, Finding(
            kind='period_failed', fingerprint=fingerprint('failed', report.start, report.end, note),
            details=f'Период {_period_label(report)} не обработан ({note}). Он будет прочитан заново при следующей синхронизации.'))
        result.periods_failed += 1
        return
    result.inserted += len(plan.inserts)
    result.updated += len(plan.updates)
    result.unchanged += plan.unchanged
    if report.complete:
        result.periods_ok += 1
    else:
        result.periods_incomplete += 1


async def run_sync(adapter: Adapter, store: Store, today: date, overlap_days: int, max_steps: int = MAX_STEPS) -> RunResult:
    start_date = await store.start_date()
    if start_date is None:
        raise NotConfigured('Не выбрана дата, с которой начинать импорт. Откройте /click → «📅 Дата начала».')

    # A run that died half-way left periods "in progress"; they are read again like any unfinished period.
    await store.recover_interrupted()
    target = sync_target(today, start_date, overlap_days, await store.history_complete(), await store.unfinished_starts())

    result = RunResult()
    seen: Set[Tuple[date, date]] = set()
    report = await adapter.open()
    for _step in range(max_steps):
        key = (report.start, report.end)
        if key in seen:     # the source answered with a page we already had: no more history
            result.end_of_history = True
            break
        seen.add(key)
        if report.end < start_date:
            result.reached_target = True
            break
        result.visited.append(key)
        await _process(store, report, start_date, result)
        if report.start <= target:
            result.reached_target = True
            break
        previous = await adapter.older()
        if previous is None:
            result.end_of_history = True
            break
        if (previous.start, previous.end) not in seen and previous.start >= report.start:
            raise SyncError('Переход к прошлому периоду открыл не более ранний период. Синхронизация остановлена, проверьте отчёты в @clickuz.')
        report = previous
    else:
        raise SyncError('Слишком много периодов за один запуск: похоже на зацикливание. Синхронизация остановлена.')

    if result.end_of_history and not result.reached_target and result.visited:
        # Not silently: the user asked for an earlier start than the source can show.
        earliest = min(start for start, _end in result.visited)
        await store.raise_finding(Finding(
            kind='history_ended_early', fingerprint=fingerprint('history_end', earliest, start_date),
            details=f'Click не показывает отчёты раньше {earliest:%d.%m.%Y}, а импорт задан с {start_date:%d.%m.%Y}. '
                    f'Операции до {earliest:%d.%m.%Y} не загружены.'))
    if (result.reached_target or result.end_of_history) and not result.periods_failed and not result.periods_incomplete:
        await store.set_history_complete()
    return result
