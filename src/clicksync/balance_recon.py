"""Reconciliation of one card's balance between two observations. Pure: no database, no Telegram.

Interval and boundary rules
---------------------------
* An interval is (start_at, end_at] between two consecutive accepted balance snapshots of the same card in
  the same currency, in UTC. The first snapshot of a card is only a baseline: nothing before it is judged.
* A payment belongs to the interval when its time (as printed by Click, converted from CLICK_TIMEZONE to
  UTC) is after start_at and not after end_at.
* The report and the balance are not one atomic snapshot: Click may print a payment a little before or after
  the moment the balance already (or not yet) reflects it. A payment within BALANCE_BOUNDARY_MINUTES of
  either end is a boundary payment. If moving any one boundary payment to the other side of its boundary
  would explain the remainder exactly, the interval is `uncertain`, not unexplained.

expected = opening + incoming - outgoing - fees charged on top
* completed payments count; failed ones never do;
* a fee counts only when the source says it is charged on top (fee_included False); an included fee is
  inside the amount already and is not subtracted twice; an unknown placement is tried both ways;
* a pending payment is tried as debited and as not yet debited; a reversed one as not moved (base) and as
  moved;
* any reading that fits exactly explains the interval (the reading is named in `reason`);
* a completed payment with an unknown direction makes the result `uncertain` (the owner sets the direction
  in /click first); a transfer between own cards is a real movement of this card's balance and counts as
  any payment.

remaining = closing - expected - explained, where `explained` is the signed sum of the owner's explanations
that are not matched to a source payment yet (a matched part is already among the payments). A positive
remaining is money that came in unexplained, a negative one money that went out unexplained.

Statuses: ok (fits), explained (fits thanks to the owner's explanations), waiting (the grace period has not
passed or no report was read after it), unexplained (ask the owner), uncertain (the data does not allow a
judgement; `reason` says why). Nothing is ever recorded from here.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

ZERO = Decimal('0')
# A report read later than this after the grace period is still missing: stop waiting, say why.
STALE_AFTER = timedelta(hours=24)
# Bound subset enumeration when many pending payments or unknown commissions are present.
MAX_READINGS = 4096


@dataclass(frozen=True)
class Movement:
    id: int
    amount: Decimal
    direction: str                  # in / out / unknown
    status: str
    at: datetime                    # UTC
    fee: Optional[Decimal] = None
    fee_included: Optional[bool] = None


@dataclass
class IntervalFacts:
    start_at: datetime
    end_at: datetime
    opening: Decimal
    closing: Decimal
    movements: Sequence[Movement]   # this card and currency, inside the interval or within the boundary margin
    explained: Decimal = ZERO
    report_read_at: Optional[datetime] = None   # when a complete report covering end_at was last read (UTC)
    report_problem: Optional[str] = None        # periods of the interval failed / incomplete
    card_problem: Optional[str] = None          # the card cannot be tied to payments unambiguously


@dataclass
class IntervalResult:
    status: str
    incoming: Decimal = ZERO
    outgoing: Decimal = ZERO
    fees: Decimal = ZERO
    expected: Optional[Decimal] = None
    difference: Optional[Decimal] = None
    remaining: Optional[Decimal] = None
    reason: Optional[str] = None
    used: List[int] = field(default_factory=list)       # ids of the payments counted in the base reading


def _sum(movements: Sequence[Movement], pending: bool, reversed_moved: bool, unknown_fee_on_top: bool):
    incoming = outgoing = fees = ZERO
    used = []
    for m in movements:
        if m.direction not in ('in', 'out') or m.status == 'failed':
            continue
        if m.status == 'pending' and not pending:
            continue
        if m.status == 'reversed' and not reversed_moved:
            continue
        if m.status not in ('completed', 'pending', 'reversed'):
            continue
        used.append(m.id)
        if m.direction == 'in':
            incoming += m.amount
        else:
            outgoing += m.amount
        if m.fee and (m.fee_included is False or (m.fee_included is None and unknown_fee_on_top)):
            fees += m.fee
    return incoming, outgoing, fees, used


READINGS = {
    'pending': 'если платёж «в обработке» ещё не списан',
    'reversed': 'если отменённый платёж всё же прошёл по карте',
    'fee': 'если комиссия уже входит в сумму платежа',
}


def _possible_readings(movements: Sequence[Movement]):
    """One auditable reading per possible net movement, with independent choices for each payment.

    Deduplicate equal net movements at every step, rather than enumerating all combinations. If the
    bounded search cannot finish, the caller must stay uncertain instead of guessing a missing payment.
    """
    readings = {ZERO: (ZERO, ZERO, ZERO, [], ())}
    for m in movements:
        if m.status == 'failed':
            continue
        options = []
        if m.status == 'reversed':
            options.append((ZERO, ZERO, ZERO, [], ()))
        fee_options = (True, False) if m.fee and m.fee_included is None else (True,)
        for fee_on_top in fee_options:
            income, expense, fees, used = _sum([m], True, True, fee_on_top)
            notes = (READINGS['reversed'],) if m.status == 'reversed' else ()
            if not fee_on_top:
                notes += (READINGS['fee'],)
            options.append((income, expense, fees, used, notes))
        if m.status == 'pending':
            options.append((ZERO, ZERO, ZERO, [], (READINGS['pending'],)))
        next_readings = {}
        for income, expense, fees, used, notes in readings.values():
            for inc, out, fee, ids, why in options:
                total = (income + inc, expense + out, fees + fee, used + ids, tuple(dict.fromkeys(notes + why)))
                net = total[0] - total[1] - total[2]
                next_readings.setdefault(net, total)
                if len(next_readings) > MAX_READINGS:
                    return None
        readings = next_readings
    return readings


def reconcile(facts: IntervalFacts, now: datetime, grace: timedelta, boundary: timedelta) -> IntervalResult:
    inside = [m for m in facts.movements if facts.start_at < m.at <= facts.end_at]
    has_pending = any(m.status == 'pending' and m.direction in ('in', 'out') for m in inside)
    has_reversed = any(m.status == 'reversed' and m.direction in ('in', 'out') for m in inside)
    has_unknown_fee = any(m.fee and m.fee_included is None and m.status != 'failed' for m in inside)

    def result_for(pending: bool, reversed_moved: bool, fee_on_top: bool, movements=inside):
        incoming, outgoing, fees, used = _sum(movements, pending, reversed_moved, fee_on_top)
        expected = facts.opening + incoming - outgoing - fees
        difference = facts.closing - expected
        return incoming, outgoing, fees, expected, difference, difference - facts.explained, used

    incoming, outgoing, fees, expected, difference, remaining, used = result_for(True, False, True)
    base = IntervalResult(status='uncertain', incoming=incoming, outgoing=outgoing, fees=fees, expected=expected,
                          difference=difference, remaining=remaining, used=used)

    if facts.card_problem:
        base.reason = facts.card_problem
        return base
    unsigned = [m for m in inside if m.direction not in ('in', 'out') and m.status != 'failed']
    if unsigned:
        base.reason = (f'у {len(unsigned)} платеж(ей) в этом промежутке неизвестно направление (расход или доход): '
                       'укажите его при разборе платежей в /click')
        return base
    if any(m.status not in ('completed', 'pending', 'reversed', 'failed') for m in inside):
        base.reason = 'у части платежей неизвестен статус: нельзя определить, изменили ли они баланс'
        return base
    if remaining == 0:
        base.status = 'explained' if facts.explained else 'ok'
        return base
    if has_pending or has_reversed or has_unknown_fee:
        readings = _possible_readings(inside)
        if readings is None:
            base.reason = 'слишком много вариантов списаний и комиссий: нельзя однозначно сверить баланс'
            return base
        target_net = facts.closing - facts.opening - facts.explained
        matching = readings.get(target_net)
        if matching is not None:
            inc, out, fee, ids, notes = matching
            expected = facts.opening + inc - out - fee
            return IntervalResult(
                status='explained' if facts.explained else 'ok', incoming=inc, outgoing=out, fees=fee,
                expected=expected, difference=facts.closing - expected, remaining=ZERO, used=ids,
                reason='сходится, ' + ' и '.join(notes) if notes else None)

    final_read = facts.report_read_at is not None and facts.report_read_at >= facts.end_at + grace
    if now < facts.end_at + grace or not final_read or facts.report_problem:
        if now < facts.end_at + grace + STALE_AFTER:
            base.status = 'waiting'
            base.reason = 'жду, пока платежи за этот промежуток появятся в отчёте Click'
            return base
        base.reason = facts.report_problem or 'после этого промежутка отчёт Click ещё не был прочитан полностью'
        return base
    if facts.explained and (remaining > 0) != (facts.explained > 0):
        # The owner's explanations now cover more than is missing: most likely the explained payment has
        # appeared in the report and is counted twice. Not new money: ask to link it instead.
        base.reason = ('объяснений больше, чем не хватает: похоже, объяснённый платёж появился в отчёте. '
                       'Свяжите его с вашим ответом при разборе платежей в /click')
        return base

    # A payment printed near a boundary may be on the other side of it in the balance.
    for m in facts.movements:
        near_start = abs(m.at - facts.start_at) <= boundary
        near_end = abs(m.at - facts.end_at) <= boundary
        if not (near_start or near_end) or m.direction not in ('in', 'out') or m.status == 'failed':
            continue
        signed = m.amount if m.direction == 'in' else -m.amount
        counted = m.id in used
        # Counted: taking it out adds it back to the remainder; not counted: taking it in subtracts it.
        if (counted and remaining + signed == 0) or (not counted and remaining - signed == 0):
            base.reason = ('платёж около момента проверки баланса мог попасть в соседний промежуток: '
                           'разница совпадает с его суммой')
            return base
    if has_pending:
        base.reason = 'есть платежи «в обработке», а разница ими не объясняется'
        return base
    if has_unknown_fee or has_reversed:
        base.reason = 'неизвестно, включены ли комиссии в суммы' if has_unknown_fee else 'есть отменённые платежи с неясным движением денег'
        return base
    base.status = 'unexplained'
    return base


def card_problem(last4: Optional[str], reply_ambiguous: bool, accounts: Sequence[Optional[str]]) -> Optional[str]:
    """Why payments cannot be tied to this card, or None. `accounts` are the payment cards of the same
    currency seen near the interval (masked as printed)."""
    if not last4:
        return 'у карты в ответе /balance не распознаны последние цифры номера'
    if reply_ambiguous:
        return f'в ответе /balance несколько карт оканчиваются на {last4}: платежи нельзя однозначно отнести к одной'
    masks = {a for a in accounts if a and a[-4:] == last4}
    if len(masks) > 1:
        return f'в отчётах есть разные карты, оканчивающиеся на {last4}: платежи нельзя однозначно отнести к одной'
    if any(not a for a in accounts):
        return 'у части платежей в отчёте не указана карта'
    return None


def explanation_effect(direction: str, amount: Decimal, matched: Decimal) -> Decimal:
    """Signed part of an explanation still counted: the matched part is among the source payments now."""
    left = max(amount - matched, ZERO)
    return left if direction == 'in' else -left


def match_candidates(explanations: Sequence[Tuple[int, str, Decimal]], payments: Sequence[Tuple[int, str, Decimal]]):
    """Strong, unique pairs (explanation id, payment id) for automatic linking.

    explanations: (id, direction, amount still unmatched); payments: (id, direction, amount) of the same card,
    currency and interval, not yet matched or recorded. A pair is linked automatically only when the amounts
    are equal, the directions agree and neither side has another candidate: amount alone is never enough
    (the caller has already required the same card, currency and interval).
    """
    pairs = [(e, p) for e in explanations for p in payments if e[1] == p[1] and e[2] == p[2] and e[2] > 0]
    result = []
    for e, p in pairs:
        if sum(1 for x, _ in pairs if x[0] == e[0]) == 1 and sum(1 for _, y in pairs if y[0] == p[0]) == 1:
            result.append((e[0], p[0]))
    return result


@dataclass(frozen=True)
class Snap:
    id: int
    observation_id: int
    card: str
    last4: Optional[str]
    currency: str
    observed_at: datetime


def pair_snapshots(snapshots: Sequence[Snap]) -> List[Tuple[int, int]]:
    """(previous id, next id) for consecutive card snapshots of the same card and currency.

    The same card is the same masked label. When a label is new (the bank renamed the card), the previous
    snapshot is taken by the last four digits only if exactly one card of the previous observation has them in
    that currency and its label is gone from the new one: otherwise the new snapshot starts a fresh baseline.
    """
    by_observation: dict = {}
    for snap in sorted(snapshots, key=lambda s: (s.observed_at, s.observation_id, s.id)):
        by_observation.setdefault(snap.observation_id, []).append(snap)
    observations = sorted(by_observation.values(), key=lambda group: group[0].observed_at)
    last_by_label: dict = {}
    previous_group: List[Snap] = []
    pairs = []
    for group in observations:
        labels_now = {(s.card, s.currency) for s in group}
        renamed = []
        for snap in group:
            unique_now = sum((s.last4, s.currency) == (snap.last4, snap.currency) for s in group) == 1
            prev = last_by_label.get((snap.card, snap.currency))
            if prev is not None and prev.card != snap.card and not unique_now:
                prev = None     # an old alias may now belong to a different card with the same ending
            if prev is None and snap.last4 and unique_now:
                same = [p for p in previous_group if (p.last4, p.currency) == (snap.last4, snap.currency)]
                same = [p for p in same if (p.card, p.currency) not in labels_now]
                if len(same) == 1:
                    prev = same[0]
            if prev is not None:
                pairs.append((prev.id, snap.id))
                renamed.append((prev, snap))
        # Carry historical aliases forward, so a label returning later starts from the latest snapshot,
        # not the old snapshot of that label. Never redirect a label still belonging to a current card.
        for prev, snap in renamed:
            for label, latest in list(last_by_label.items()):
                if latest.id == prev.id and label not in labels_now:
                    last_by_label[label] = snap
        for snap in group:
            last_by_label[(snap.card, snap.currency)] = snap
        previous_group = group
    return pairs
