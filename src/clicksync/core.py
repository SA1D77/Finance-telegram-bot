"""Pure logic of the import: no Telegram and no database, so every rule here is covered by plain unit tests.

Deduplication (documented strategy)
-----------------------------------
1. A transaction with the source's own id is keyed by that id: `id:<sha1>`.
2. Without one the key is a hash of what identifies a payment in the report: masked account, time to the
   minute as printed, amount, currency and the description (case and spaces ignored), plus the occurrence
   number among identical rows of the same period report: `h:<sha256>:<n>`. The direction is left out: the
   monthly file has no sign, so it may become known only on a later read, and must not create a second row.
   Reading the same or an overlapping report again gives the same keys, so nothing is inserted twice.
3. Rows that had an identical twin are flagged `ambiguous`: two equal payments in one minute cannot be told
   apart from one payment printed twice, and the user is shown that flag when classifying.
The status is not part of the key, so pending -> completed updates the stored row instead of adding one.
An 'unknown' status or direction never overwrites a known one.
"""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class ParsedTx:
    """One transaction as the source reports it. Amounts are positive; the direction says which way."""
    amount: Decimal
    direction: str                          # models.DIRECTIONS
    status: str                             # models.STATUSES
    occurred_at: Optional[datetime] = None  # wall-clock time as printed
    currency: Optional[str] = None
    description: Optional[str] = None
    account: Optional[str] = None           # already masked
    source_id: Optional[str] = None
    fee: Optional[Decimal] = None
    fee_included: Optional[bool] = None


@dataclass
class PeriodReport:
    """Everything the adapter read for one report period (all pages together)."""
    start: date
    end: date
    transactions: List[ParsedTx]
    pages: int = 1
    complete: bool = True               # False: the adapter knows it could not read everything
    note: Optional[str] = None          # why it is incomplete
    opening_balance: Optional[Decimal] = None
    closing_balance: Optional[Decimal] = None
    balance_account: Optional[str] = None
    balance_currency: Optional[str] = None


@dataclass
class StoredTx:
    """The stored fields the ingest compares against."""
    id: int
    dedup_key: str
    status: str
    amount: Decimal
    delivery: str
    fee: Optional[Decimal] = None
    fee_included: Optional[bool] = None
    description: Optional[str] = None
    source_id: Optional[str] = None
    ambiguous: bool = False
    direction: str = 'unknown'
    account: Optional[str] = None
    occurred_at: Optional[datetime] = None
    currency: Optional[str] = None


@dataclass
class TxUpdate:
    stored: StoredTx
    changes: Dict[str, Tuple[Any, Any]]     # field -> (old, new)
    # A transaction the user already recorded stopped being a completed payment or changed its amount.
    needs_review: bool = False


@dataclass
class IngestPlan:
    inserts: List[Tuple[str, bool, ParsedTx]] = field(default_factory=list)   # (key, ambiguous, tx)
    updates: List[TxUpdate] = field(default_factory=list)
    unchanged: int = 0


_MASK_CHARS = '*•×xX'


def mask_account(raw: Optional[str]) -> Optional[str]:
    """Card / account number safe to store: the source's own mask is kept, a full number is masked here."""
    if not raw:
        return None
    text = raw.strip()[:32]
    digits = sum(ch.isdigit() for ch in text)
    if digits < 9 or any(ch in _MASK_CHARS for ch in text):
        return text or None
    keep_head = 6 if digits >= 12 else 0
    out, seen = [], 0
    for ch in text:
        if ch.isdigit():
            seen += 1
            out.append(ch if seen <= keep_head or seen > digits - 4 else '*')
        else:
            out.append(ch)
    return ''.join(out)


def _plain(value: Decimal) -> str:
    return format(value.normalize(), 'f')


def _identity(tx: ParsedTx) -> str:
    description = ' '.join((tx.description or '').casefold().split())
    when = tx.occurred_at.strftime('%Y-%m-%dT%H:%M') if tx.occurred_at else ''
    return '|'.join([tx.account or '', when, _plain(tx.amount), (tx.currency or '').upper(), description])


def dedup_keys(transactions: Sequence[ParsedTx]) -> List[Tuple[str, bool]]:
    """(key, ambiguous) for every transaction of one period report, in the same order."""
    identities = [_identity(tx) for tx in transactions]
    totals: Dict[str, int] = {}
    for tx, identity in zip(transactions, identities):
        if not tx.source_id:
            totals[identity] = totals.get(identity, 0) + 1
    seen: Dict[str, int] = {}
    keys = []
    for tx, identity in zip(transactions, identities):
        if tx.source_id:
            keys.append(('id:' + hashlib.sha1(tx.source_id.encode()).hexdigest(), False))
            continue
        number = seen.get(identity, 0)
        seen[identity] = number + 1
        digest = hashlib.sha256(identity.encode()).hexdigest()[:56]
        keys.append((f'h:{digest}:{number}', totals[identity] > 1))
    return keys


def _minute(value: Optional[datetime]) -> Optional[datetime]:
    return value.replace(second=0, microsecond=0) if value else None


def _same_payment(tx: ParsedTx, stored: StoredTx) -> bool:
    """A candidate correction. Merchant changes need the exact timestamp and unchanged amount;
    amount/id changes still need the same merchant. The caller checks uniqueness in both directions."""
    if stored.occurred_at is None or tx.occurred_at is None:
        return False

    def words(text: Optional[str]) -> str:
        return ' '.join((text or '').casefold().split())

    same = ((stored.account or '') == (tx.account or ''), _minute(stored.occurred_at) == _minute(tx.occurred_at),
            (stored.currency or '').upper() == (tx.currency or '').upper())
    if not all(same):
        return False
    if tx.source_id and stored.source_id and tx.source_id != stored.source_id:
        return False    # two payments, each with its own id
    if words(stored.description) != words(tx.description):
        return bool(tx.account and tx.currency and tx.amount == stored.amount and tx.occurred_at == stored.occurred_at)
    return tx.amount != stored.amount or bool(tx.source_id) != bool(stored.source_id)


def plan_ingest(existing: Dict[str, StoredTx], transactions: Sequence[ParsedTx],
                keys: Sequence[Tuple[str, bool]], lookalikes: Sequence[StoredTx] = ()) -> IngestPlan:
    """What reading this report changes: new rows, corrected rows (with what changed) and untouched ones.

    `lookalikes` are stored rows near the unmatched transactions (same card and minute). The key holds the
    amount and the source's id, so a corrected amount or an id that appears later gives a new key; such a
    row is matched to the stored one and re-keyed instead of being inserted a second time. Only a stored row
    that no transaction of this report matched by key can be taken, and only with a unique match in both
    directions. Merchant corrections additionally require the exact timestamp and unchanged amount.
    """
    plan = IngestPlan()
    planned = set()
    matched_ids = {existing[key].id for key, _ in keys if key in existing}
    unmatched = list({key: tx for tx, (key, _) in zip(transactions, keys) if key not in existing}.values())
    taken: set = set()
    for tx, (key, ambiguous) in zip(transactions, keys):
        if key in planned:      # the same source id printed twice in one report
            plan.unchanged += 1
            continue
        planned.add(key)
        stored = existing.get(key)
        rekey: Dict[str, Tuple[Any, Any]] = {}
        if stored is None:
            fits = [row for row in lookalikes
                    if row.id not in matched_ids and row.id not in taken and _same_payment(tx, row)]
            if len(fits) != 1 or sum(_same_payment(candidate, fits[0]) for candidate in unmatched) != 1:
                plan.inserts.append((key, ambiguous or bool(fits), tx))
                continue
            stored = fits[0]
            taken.add(stored.id)
            if not (tx.source_id is None and stored.source_id and tx.amount == stored.amount):
                rekey['dedup_key'] = (stored.dedup_key, key)
        changes: Dict[str, Tuple[Any, Any]] = dict(rekey)
        if tx.status != stored.status and tx.status != 'unknown':
            changes['status'] = (stored.status, tx.status)
        if tx.amount != stored.amount:
            changes['amount'] = (stored.amount, tx.amount)
        if tx.direction != stored.direction and tx.direction != 'unknown':
            changes['direction'] = (stored.direction, tx.direction)
        for name in ('fee', 'fee_included', 'description', 'source_id'):
            new = getattr(tx, name)
            if new is not None and new != getattr(stored, name):
                changes[name] = (getattr(stored, name), new)
        if ambiguous and not stored.ambiguous:
            changes['ambiguous'] = (False, True)
        if not changes:
            plan.unchanged += 1
            continue
        recorded = stored.delivery in ('delivered', 'linked')
        became_invalid = 'status' in changes and stored.status == 'completed'
        turned = 'direction' in changes and stored.direction != 'unknown'
        reviewed_change = became_invalid or turned or 'amount' in changes or 'description' in changes
        plan.updates.append(TxUpdate(stored=stored, changes=changes,
                                     needs_review=recorded and reviewed_change))
    return plan


def sync_target(today: date, start_date: date, overlap_days: int, history_complete: bool,
                unfinished_starts: Iterable[date] = ()) -> date:
    """A run walks back through the periods until one starts on or before this date.

    First import: all the way to the chosen start date. Later: the overlap window (delayed transactions,
    status changes) and back to the earliest period that is not completed, so a failed one is never skipped.
    """
    if not history_complete:
        return start_date
    target = today - timedelta(days=max(overlap_days, 0))
    for start in unfinished_starts:
        target = min(target, start)
    return max(target, start_date)


_PERIOD_RE = re.compile(r'(\d{1,2})[./](\d{1,2})[./](\d{4})\s*(?:до|по|[-–—]|to)\s*(\d{1,2})[./](\d{1,2})[./](\d{4})',
                        re.IGNORECASE)


def parse_period(text: Optional[str]) -> Optional[Tuple[date, date]]:
    """"Платежи c 17.10.2026 до 24.10.2026" -> (2026-10-17, 2026-10-24); None when there is no such header."""
    match = _PERIOD_RE.search(text or '')
    if not match:
        return None
    d1, m1, y1, d2, m2, y2 = (int(x) for x in match.groups())
    try:
        start, end = date(y1, m1, d1), date(y2, m2, d2)
    except ValueError:
        return None
    return (start, end) if start <= end else None


def fingerprint(*parts: Any) -> str:
    return hashlib.sha256('|'.join('' if p is None else str(p) for p in parts).encode()).hexdigest()
