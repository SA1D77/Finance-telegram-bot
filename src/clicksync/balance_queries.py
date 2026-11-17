"""Database side of the card balance checks: snapshots, intervals, their recalculation, the owner's
explanations and their matching to source payments. Every query is scoped to the user; every write that has
to stay consistent is one transaction, and the idempotent ones are protected by unique constraints.
"""
import json
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from accounting.models import Accounting, OperationType
from middleware import service
from timeutils import utc_now
from .balance import BalanceReply
from .balance_recon import (ZERO, IntervalFacts, Movement, Snap, card_problem, explanation_effect, match_candidates,
                            pair_snapshots, reconcile)
from .models import (BalanceExplanation, BalanceInterval, BalanceIntervalAudit, BalanceObservation, BalanceSnapshot,
                     ExplanationMatch, SourceTransaction, SyncPeriod, SyncState)
from .queries import SOURCE, _audit, _session, _set_state

_EXPLANATION_NAMESPACE = uuid.UUID('2b7d4e9a-61c3-4f85-9e0a-7c3d5b1f8e24')
ACTIVE = ('unexplained', 'uncertain', 'waiting')


def explanation_operation_id(user_id: int, token: str) -> uuid.UUID:
    """Id of the accounting operation created from one confirmed explanation: the same for a repeated click."""
    return uuid.uuid5(_EXPLANATION_NAMESPACE, f'{SOURCE}:{user_id}:{token}')


def _json(value: Dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _interval_audit(interval_id: int, event: str, status: Optional[str] = None, remaining: Optional[Decimal] = None,
                    **details):
    return insert(BalanceIntervalAudit).values(interval_id=interval_id, at=utc_now(), event=event, status=status,
                                               remaining=remaining, details=_json(details) if details else None)


# --- State and schedule ------------------------------------------------------------------------------------

async def request_balance(user_id: int) -> None:
    await _set_state(user_id, balance_requested=True)


async def mark_balance(user_id: int, error: Optional[str]) -> None:
    now = utc_now()
    values: Dict[str, Any] = {'balance_requested': False, 'last_balance_at': now,
                              'last_balance_error': error[:255] if error else None}
    if not error:
        values['last_balance_ok_at'] = now
    await _set_state(user_id, **values)


async def claim_review(user_id: int, today: date) -> Optional[date]:
    """Marks today's review as taken, atomically: (previous date) when this caller should send it, else None.

    The previous date is returned so that a failed delivery can be given back (release_review)."""
    async with _session() as session, session.begin():
        previous = (await session.execute(
            select(SyncState.last_review_on).where(SyncState.user_id == user_id, SyncState.source == SOURCE)
            .with_for_update())).first()
        if previous is None or (previous[0] is not None and previous[0] >= today):
            return None
        await session.execute(update(SyncState).where(SyncState.user_id == user_id, SyncState.source == SOURCE)
                              .values(last_review_on=today))
    return previous[0] or date.min


async def release_review(user_id: int, today: date, previous: date) -> None:
    await service._execute_insert_update_delete_command(
        update(SyncState).where(SyncState.user_id == user_id, SyncState.source == SOURCE, SyncState.last_review_on == today)
        .values(last_review_on=None if previous == date.min else previous))


# --- Observations ------------------------------------------------------------------------------------------

async def save_observation(user_id: int, requested_at: datetime, observed_at: datetime, message_id: Optional[int],
                           reply: BalanceReply) -> int:
    """The accepted reply as snapshots: the aggregate per currency (kind 'total') and each card."""
    async with _session() as session, session.begin():
        observation_id = (await session.execute(
            insert(BalanceObservation).values(user_id=user_id, source=SOURCE, requested_at=requested_at,
                                              observed_at=observed_at, message_id=message_id, status='ok')
            .returning(BalanceObservation.id))).scalar()
        for currency, amount in reply.total.items():
            await session.execute(insert(BalanceSnapshot).values(
                observation_id=observation_id, user_id=user_id, source=SOURCE, kind='total', card='', last4=None,
                ambiguous=False, currency=currency, amount=amount, observed_at=observed_at))
        for card in reply.cards:
            await session.execute(insert(BalanceSnapshot).values(
                observation_id=observation_id, user_id=user_id, source=SOURCE, kind='card', card=card.label,
                last4=card.last4, ambiguous=card.last4 in reply.ambiguous_last4, currency=card.currency,
                amount=card.amount, observed_at=observed_at))
    return observation_id


async def save_rejection(user_id: int, requested_at: datetime, observed_at: datetime, error: str) -> None:
    """A reply that could not be read: kept as a fact (no amount), the snapshots stay as they were."""
    await service._execute_insert_update_delete_command(insert(BalanceObservation).values(
        user_id=user_id, source=SOURCE, requested_at=requested_at, observed_at=observed_at, status='rejected',
        error=error[:255]))


async def latest_balances(user_id: int) -> Tuple[Optional[datetime], List[BalanceSnapshot]]:
    last = (await service._execute_select_command(
        select(BalanceObservation).where(BalanceObservation.user_id == user_id, BalanceObservation.source == SOURCE,
                                         BalanceObservation.status == 'ok')
        .order_by(BalanceObservation.observed_at.desc()).limit(1)))
    if not last:
        return None, []
    snapshots = await service._execute_select_command(
        select(BalanceSnapshot).where(BalanceSnapshot.observation_id == last[0].id, BalanceSnapshot.user_id == user_id)
        .order_by(BalanceSnapshot.kind.desc(), BalanceSnapshot.card))
    return last[0].observed_at, list(snapshots)


# --- Intervals ---------------------------------------------------------------------------------------------

async def build_intervals(user_id: int) -> int:
    """Creates the interval between every two consecutive snapshots of a card (idempotent)."""
    rows = list(await service._execute_select_command(
        select(BalanceSnapshot).where(BalanceSnapshot.user_id == user_id, BalanceSnapshot.source == SOURCE,
                                      BalanceSnapshot.kind == 'card')))
    by_id = {row.id: row for row in rows}
    pairs = pair_snapshots([Snap(id=r.id, observation_id=r.observation_id, card=r.card, last4=r.last4,
                                 currency=r.currency, observed_at=r.observed_at) for r in rows])
    created = 0
    for previous, current in pairs:
        start, end = by_id[previous], by_id[current]
        result = await service._execute_insert_update_delete_command(
            insert(BalanceInterval).values(
                user_id=user_id, source=SOURCE, card=end.card, last4=end.last4, currency=end.currency,
                start_snapshot_id=start.id, end_snapshot_id=end.id, start_at=start.observed_at, end_at=end.observed_at,
                opening=start.amount, closing=end.amount, status='waiting', created_at=utc_now())
            .on_conflict_do_nothing(constraint='uq_balance_intervals_pair'))
        created += result.rowcount or 0
    return created


async def _matched_sums(explanation_ids: List[int]) -> Dict[int, Decimal]:
    if not explanation_ids:
        return {}
    rows = await service._execute_select_rows(
        select(ExplanationMatch.explanation_id, func.sum(ExplanationMatch.amount))
        .where(ExplanationMatch.explanation_id.in_(explanation_ids)).group_by(ExplanationMatch.explanation_id))
    return {explanation_id: total for explanation_id, total in rows}


def _days(first: date, last: date) -> List[date]:
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def _coverage(periods: List[SyncPeriod], start_date: Optional[date], first: date, last: date):
    """(when a complete report covering every day of the interval was last read, a problem or None)."""
    if start_date is None or first < start_date:
        return None, 'платежи раньше даты начала импорта не загружаются, сверить этот промежуток нельзя'
    read_at = None
    for day in _days(first, last):
        covering = [p for p in periods if p.period_start <= day <= p.period_end]
        bad = [p for p in covering if p.status in ('failed', 'incomplete')]
        if bad:
            p = bad[0]
            return None, f'отчёт Click за {p.period_start:%d.%m}–{p.period_end:%d.%m.%Y} прочитан не полностью'
        done = [p.finished_at for p in covering if p.status == 'completed' and p.finished_at]
        if not done:
            return None, None
        read_at = max(done) if read_at is None else min(read_at, max(done))
    return read_at, None


async def recompute(user_id: int, now: datetime, grace: timedelta, boundary: timedelta, recheck_days: int,
                    to_utc: Callable[[datetime], datetime], from_utc: Callable[[datetime], datetime]) -> Dict[str, int]:
    """Recalculates every recent interval from the current source payments and explanations.

    A change of the result is stored with an audit row (what was counted). An interval that was unexplained
    and is now fully explained by the source is closed automatically ('auto_explained').
    """
    intervals = list(await service._execute_select_command(
        select(BalanceInterval).where(BalanceInterval.user_id == user_id, BalanceInterval.source == SOURCE,
                                      BalanceInterval.end_at >= now - timedelta(days=recheck_days))))
    if not intervals:
        return {}
    periods = list(await service._execute_select_command(
        select(SyncPeriod).where(SyncPeriod.user_id == user_id, SyncPeriod.source == SOURCE)))
    state = (await service._execute_select_command(
        select(SyncState).where(SyncState.user_id == user_id, SyncState.source == SOURCE)))
    start_date = state[0].start_date if state else None
    snapshot_ids = {i.start_snapshot_id for i in intervals} | {i.end_snapshot_id for i in intervals}
    ambiguous = {s.id: s.ambiguous for s in await service._execute_select_command(
        select(BalanceSnapshot).where(BalanceSnapshot.id.in_(snapshot_ids), BalanceSnapshot.user_id == user_id))}
    explanations = list(await service._execute_select_command(
        select(BalanceExplanation).where(BalanceExplanation.user_id == user_id,
                                         BalanceExplanation.interval_id.in_([i.id for i in intervals]))))
    matched = await _matched_sums([e.id for e in explanations])
    counts: Dict[str, int] = {}
    for interval in intervals:
        local_from, local_to = from_utc(interval.start_at - boundary), from_utc(interval.end_at + boundary)
        payments = list(await service._execute_select_command(
            select(SourceTransaction).where(
                SourceTransaction.user_id == user_id, SourceTransaction.source == SOURCE,
                SourceTransaction.currency == interval.currency,
                SourceTransaction.occurred_at >= local_from, SourceTransaction.occurred_at <= local_to)))
        relevant = [p for p in payments if not p.account or p.account[-4:] == interval.last4]
        reply_ambiguous = any(ambiguous.get(i, False) for i in (interval.start_snapshot_id, interval.end_snapshot_id))
        problem = card_problem(interval.last4, reply_ambiguous, [p.account for p in relevant])
        mine = [p for p in relevant if p.account]
        own = [e for e in explanations if e.interval_id == interval.id]
        explained = sum((explanation_effect(e.direction, e.amount, matched.get(e.id, ZERO)) for e in own), ZERO)
        read_at, report_problem = _coverage(periods, start_date, from_utc(interval.start_at).date(),
                                            from_utc(interval.end_at).date())
        facts = IntervalFacts(
            start_at=interval.start_at, end_at=interval.end_at, opening=interval.opening, closing=interval.closing,
            movements=[Movement(id=p.id, amount=p.amount, direction=p.direction, status=p.status,
                                at=to_utc(p.occurred_at), fee=p.fee, fee_included=p.fee_included) for p in mine],
            explained=explained, report_read_at=read_at, report_problem=report_problem, card_problem=problem)
        result = reconcile(facts, now, grace, boundary)
        counts[result.status] = counts.get(result.status, 0) + 1
        before = (interval.status, interval.remaining, interval.expected, interval.reason or None, interval.explained)
        if before == (result.status, result.remaining, result.expected, result.reason or None, explained):
            continue
        event = ('auto_explained' if interval.status in ('unexplained', 'uncertain') and result.status in ('ok', 'explained')
                 else 'computed')
        async with _session() as session, session.begin():
            await session.execute(update(BalanceInterval).where(BalanceInterval.id == interval.id,
                                                                BalanceInterval.user_id == user_id).values(
                incoming=result.incoming, outgoing=result.outgoing, fees=result.fees, expected=result.expected,
                difference=result.difference, explained=explained, remaining=result.remaining, status=result.status,
                reason=result.reason, computed_at=now))
            await session.execute(_interval_audit(
                interval.id, event, result.status, result.remaining, payments=result.used, reason=result.reason,
                explanations=[e.id for e in own], expected=result.expected, previous=interval.status))
    return counts


# --- Explanations --------------------------------------------------------------------------------------------

async def explain(user_id: int, interval_id: int, token: str, kind: str, direction: str, amount: Decimal,
                  category_id: Optional[int] = None, operation: Optional[Dict[str, Any]] = None) -> str:
    """Stores the owner's explanation (and the accounting operation, when `operation` is given) atomically.

    'ok'; 'duplicate' when this token was confirmed before (nothing new is written); 'too_much' when the amount
    exceeds what is unexplained or goes the other way; 'stale' when the interval is gone or not open.
    """
    signed = amount if direction == 'in' else -amount
    if amount <= 0 or direction not in ('in', 'out'):
        return 'too_much'
    async with _session() as session, session.begin():
        interval = (await session.execute(
            select(BalanceInterval).where(BalanceInterval.id == interval_id, BalanceInterval.user_id == user_id)
            .with_for_update())).scalar()
        if interval is None:
            return 'stale'
        done = (await session.execute(select(BalanceExplanation.id).where(
            BalanceExplanation.user_id == user_id, BalanceExplanation.token == token))).scalar()
        if done is not None:
            return 'duplicate'
        remaining = interval.remaining
        if interval.status not in ('unexplained', 'uncertain') or not remaining:
            return 'stale'
        if (remaining > 0) != (signed > 0) or abs(signed) > abs(remaining):
            return 'too_much'
        operation_id = None
        if operation is not None:
            operation_id = explanation_operation_id(user_id, token)
            await session.execute(insert(Accounting).values(id=operation_id, user_id=user_id, category_id=category_id,
                                                            **operation))
        explanation_id = (await session.execute(insert(BalanceExplanation).values(
            user_id=user_id, interval_id=interval_id, token=token, kind=kind, direction=direction, amount=amount,
            currency=interval.currency, category_id=category_id, accounting_id=operation_id, created_at=utc_now())
            .returning(BalanceExplanation.id))).scalar()
        left = remaining - signed
        await session.execute(update(BalanceInterval).where(BalanceInterval.id == interval_id).values(
            explained=(interval.explained or ZERO) + signed, remaining=left,
            status='explained' if left == 0 else interval.status, unresolved=False if left == 0 else interval.unresolved))
        await session.execute(_interval_audit(interval_id, 'explained', 'explained' if left == 0 else interval.status, left,
                                              explanation=explanation_id, kind=kind, amount=amount, category=category_id,
                                              operation=operation_id))
    return 'ok'


async def mark_unresolved(user_id: int, interval_id: int) -> bool:
    """"Не помню": stays open as it is, nothing is recorded, it is not asked again."""
    async with _session() as session, session.begin():
        row = (await session.execute(update(BalanceInterval).where(
            BalanceInterval.id == interval_id, BalanceInterval.user_id == user_id,
            BalanceInterval.status.in_(ACTIVE)).values(unresolved=True).returning(BalanceInterval.remaining))).first()
        if row is None:
            return False
        await session.execute(_interval_audit(interval_id, 'unresolved', remaining=row[0]))
    return True


async def snooze(user_id: int, interval_id: int, until: datetime) -> bool:
    async with _session() as session, session.begin():
        row = (await session.execute(update(BalanceInterval).where(
            BalanceInterval.id == interval_id, BalanceInterval.user_id == user_id,
            BalanceInterval.status.in_(ACTIVE)).values(snoozed_until=until).returning(BalanceInterval.id))).first()
        if row is None:
            return False
        await session.execute(_interval_audit(interval_id, 'snoozed', until=until))
    return True


# --- Matching explanations to payments that appear later -------------------------------------------------

async def apply_match(user_id: int, explanation_id: int, transaction_id: int, reason: str,
                      boundary: timedelta = timedelta(0), to_utc: Optional[Callable] = None) -> bool:
    """Ties a source payment to (a part of) an explanation: the payment is not offered for recording again and
    is linked to the explanation's operation, so nothing is counted or recorded twice."""
    async with _session() as session, session.begin():
        explanation = (await session.execute(select(BalanceExplanation).where(
            BalanceExplanation.id == explanation_id, BalanceExplanation.user_id == user_id).with_for_update())).scalar()
        tx = (await session.execute(select(SourceTransaction).where(
            SourceTransaction.id == transaction_id, SourceTransaction.user_id == user_id,
            SourceTransaction.source == SOURCE, SourceTransaction.delivery == 'new').with_for_update())).scalar()
        if explanation is None or tx is None:
            return False
        interval = (await session.execute(select(BalanceInterval).where(BalanceInterval.id == explanation.interval_id))).scalar()
        same_card = bool(tx.account) and tx.account[-4:] == interval.last4
        if not same_card or (tx.currency, tx.direction) != (explanation.currency, explanation.direction):
            return False
        if tx.status not in ('completed', 'pending'):
            return False
        if to_utc is not None and tx.occurred_at is not None:
            at = to_utc(tx.occurred_at)
            if not interval.start_at - boundary < at <= interval.end_at + boundary:
                return False
        used = (await session.execute(select(func.coalesce(func.sum(ExplanationMatch.amount), 0)).where(
            ExplanationMatch.explanation_id == explanation_id))).scalar()
        if tx.amount > explanation.amount - used:
            return False
        inserted = (await session.execute(insert(ExplanationMatch).values(
            explanation_id=explanation_id, transaction_id=transaction_id, amount=tx.amount, reason=reason,
            created_at=utc_now()).on_conflict_do_nothing(constraint='uq_balance_explanation_matches_tx')
            .returning(ExplanationMatch.id))).scalar()
        if inserted is None:
            return False
        delivery = 'linked' if explanation.accounting_id else 'ignored'
        await session.execute(update(SourceTransaction).where(SourceTransaction.id == transaction_id).values(
            delivery=delivery, accounting_id=explanation.accounting_id, own_transfer=explanation.kind == 'own_transfer',
            updated_at=utc_now()))
        await session.execute(_audit(transaction_id, 'delivery', 'new', delivery, 'balance_explanation'))
        await session.execute(_interval_audit(interval.id, 'matched', explanation=explanation_id, payment=transaction_id,
                                              amount=tx.amount, by=reason))
    return True


async def auto_match(user_id: int, to_utc: Callable, from_utc: Callable) -> int:
    """Links payments to explanations only when the pair is strong and unique (see balance_recon.match_candidates)."""
    explanations = list(await service._execute_select_command(
        select(BalanceExplanation).where(BalanceExplanation.user_id == user_id)))
    if not explanations:
        return 0
    matched = await _matched_sums([e.id for e in explanations])
    open_ones = [e for e in explanations if e.amount - matched.get(e.id, ZERO) > 0]
    linked = 0
    for interval_id in sorted({e.interval_id for e in open_ones}):
        interval = (await service._execute_select_command(
            select(BalanceInterval).where(BalanceInterval.id == interval_id, BalanceInterval.user_id == user_id)))[0]
        taken = select(ExplanationMatch.transaction_id)
        payments = list(await service._execute_select_command(select(SourceTransaction).where(
            SourceTransaction.user_id == user_id, SourceTransaction.source == SOURCE,
            SourceTransaction.currency == interval.currency, SourceTransaction.delivery == 'new',
            SourceTransaction.status == 'completed', SourceTransaction.id.notin_(taken),
            SourceTransaction.occurred_at > from_utc(interval.start_at),
            SourceTransaction.occurred_at <= from_utc(interval.end_at))))
        payments = [p for p in payments if p.account and p.account[-4:] == interval.last4]
        pairs = match_candidates(
            [(e.id, e.direction, e.amount - matched.get(e.id, ZERO)) for e in open_ones if e.interval_id == interval_id],
            [(p.id, p.direction, p.amount) for p in payments])
        for explanation_id, payment_id in pairs:
            if await apply_match(user_id, explanation_id, payment_id, 'auto'):
                linked += 1
    return linked


async def explanation_candidates(user_id: int, transaction_id: int, boundary: timedelta,
                                 to_utc: Callable) -> List[Tuple[BalanceExplanation, BalanceInterval, Decimal]]:
    """Explanations this waiting payment may be (a part of): same card, currency and direction, the payment's
    time within the explained interval, and enough left unmatched. For the owner to choose; never automatic."""
    tx = (await service._execute_select_command(select(SourceTransaction).where(
        SourceTransaction.id == transaction_id, SourceTransaction.user_id == user_id, SourceTransaction.delivery == 'new')))
    if not tx or not tx[0].account or tx[0].occurred_at is None:
        return []
    tx = tx[0]
    at = to_utc(tx.occurred_at)
    rows = list(await service._execute_select_rows(select(BalanceExplanation, BalanceInterval).join(
        BalanceInterval, BalanceInterval.id == BalanceExplanation.interval_id).where(
        BalanceExplanation.user_id == user_id, BalanceExplanation.currency == tx.currency,
        BalanceExplanation.direction == tx.direction, BalanceInterval.last4 == tx.account[-4:],
        BalanceInterval.start_at - boundary < at, BalanceInterval.end_at + boundary >= at)))
    matched = await _matched_sums([e.id for e, _ in rows])
    result = []
    for explanation, interval in rows:
        left = explanation.amount - matched.get(explanation.id, ZERO)
        if left >= tx.amount:
            result.append((explanation, interval, left))
    return result


# --- What the bot shows --------------------------------------------------------------------------------------

def _askable(user_id: int, now: datetime):
    return and_(BalanceInterval.user_id == user_id, BalanceInterval.source == SOURCE,
                BalanceInterval.status == 'unexplained', BalanceInterval.unresolved.is_(False),
                or_(BalanceInterval.snoozed_until.is_(None), BalanceInterval.snoozed_until <= now))


async def open_intervals(user_id: int, now: datetime) -> List[BalanceInterval]:
    """Unexplained, not marked "Не помню" and not snoozed: the questions to the owner, by card and time."""
    return list(await service._execute_select_command(
        select(BalanceInterval).where(_askable(user_id, now)).order_by(BalanceInterval.card, BalanceInterval.start_at)))


async def other_intervals(user_id: int, now: datetime) -> List[BalanceInterval]:
    """Uncertain, waiting, "Не помню" and snoozed ones: shown, but not asked about."""
    statement = select(BalanceInterval).where(
        BalanceInterval.user_id == user_id, BalanceInterval.source == SOURCE,
        BalanceInterval.status.in_(ACTIVE), ~_askable(user_id, now)).order_by(BalanceInterval.card, BalanceInterval.start_at)
    return list(await service._execute_select_command(statement))


async def unnotified_intervals(user_id: int, now: datetime) -> List[BalanceInterval]:
    return list(await service._execute_select_command(
        select(BalanceInterval).where(_askable(user_id, now), BalanceInterval.notified_at.is_(None))
        .order_by(BalanceInterval.card, BalanceInterval.start_at)))


async def mark_intervals_notified(user_id: int, ids: List[int]) -> None:
    if ids:
        await service._execute_insert_update_delete_command(
            update(BalanceInterval).where(BalanceInterval.user_id == user_id, BalanceInterval.id.in_(ids))
            .values(notified_at=utc_now()))


async def get_interval(user_id: int, interval_id: int) -> Optional[BalanceInterval]:
    rows = await service._execute_select_command(
        select(BalanceInterval).where(BalanceInterval.id == interval_id, BalanceInterval.user_id == user_id))
    return rows[0] if rows else None


async def interval_explanations(user_id: int, interval_id: int) -> List[BalanceExplanation]:
    return list(await service._execute_select_command(
        select(BalanceExplanation).where(BalanceExplanation.user_id == user_id, BalanceExplanation.interval_id == interval_id)
        .order_by(BalanceExplanation.id)))


def operation_values(quantity: Decimal, operation_type: OperationType, comment: str, created_at: datetime,
                     original_amount: Optional[Decimal], original_currency: Optional[str], rate: Optional[Decimal]) -> Dict[str, Any]:
    return dict(quantity=quantity, operation_type=operation_type, comment=comment[:255], created_at=created_at,
                original_amount=original_amount, original_currency=original_currency, rate=rate)
