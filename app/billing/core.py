"""The billing core: what one account's run at `as_of` must write.

Expected-vs-actual diff. For every product and cycle period whose first billable day is
<= as_of, the expected net is the recurring price x billable days / period days, rounded
once (ROUND_HALF_EVEN). The stored ABRs of that period are compared with it and only the
difference is emitted: a charge when nothing is stored, a refund (expected - charged) when
the expected net dropped because a termination became known later. Pricing the unused
days separately instead would let cent drift accumulate.

Bills: a bill on cycle day D, for [D, next D), collects the unbilled ABRs whose run date is
<= D. A run issues the bills for every cycle day after the last bill up to as_of, skipping
those that would be empty.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID
from zoneinfo import ZoneInfo

from app.billing.model import (
    AbrKind,
    Account,
    BillingResult,
    BillingState,
    DateRange,
    InconsistentState,
    MissingPrice,
    NewAbr,
    NewBill,
    Price,
    Product,
    StoredAbr,
)

CENT = Decimal("0.01")
NOT_BILLABLE = frozenset({"cancelled", "aborted"})


def add_month(day: date) -> date:
    """The same day one month later. Only called with days 1..28, which exist every month."""
    if day.month == 12:
        return day.replace(year=day.year + 1, month=1)
    return day.replace(month=day.month + 1)


def sub_month(day: date) -> date:
    if day.month == 1:
        return day.replace(year=day.year - 1, month=12)
    return day.replace(month=day.month - 1)


def cycle_period(day: date, cycle_day: int) -> DateRange:
    """The billing period [cycle day of month M, cycle day of month M+1) containing `day`."""
    if not 1 <= cycle_day <= 28:
        raise ValueError(f"bill cycle day {cycle_day} not in 1..28")
    start = day.replace(day=cycle_day)
    if day.day < cycle_day:
        start = sub_month(start)
    return DateRange(start, add_month(start))


def billable_range(product: Product, period: DateRange) -> DateRange:
    """The days of `period` the product is billable on: start_date <= d < termination_date,
    never for cancelled or aborted products. Empty if there are none."""
    if product.status in NOT_BILLABLE:
        return DateRange(period.start, period.start)
    start = max(product.start_date, period.start)
    end = period.end
    if product.termination_date is not None:
        end = min(end, product.termination_date)
    if end <= start:
        return DateRange(start, start)
    return DateRange(start, end)


def prorate(amount: Decimal, days: int, period: DateRange) -> Decimal:
    """amount x days / period days, rounded once, half-even, to cents."""
    if not isinstance(amount, Decimal):
        raise TypeError(f"money must be Decimal, got {type(amount).__name__}")
    with localcontext(prec=50):
        exact = amount * days / period.days
    return exact.quantize(CENT, rounding=ROUND_HALF_EVEN)


def midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time(0), tzinfo=tz)


def price_at(
    product: Product, day: date, prices: tuple[Price, ...], currency: str, tz: ZoneInfo
) -> Price:
    """The offering's recurring price in `currency` valid at midnight (tz) of `day`."""
    at = midnight(day, tz)
    found = [
        p
        for p in prices
        if p.offering_id == product.offering_id
        and p.currency == currency
        and p.valid_from <= at
        and (p.valid_to is None or at < p.valid_to)
    ]
    if not found:
        raise MissingPrice(product.id, day, currency)
    if len(found) > 1:
        raise InconsistentState(f"product {product.id}: {len(found)} prices valid on {day}")
    return found[0]


def compute(state: BillingState, as_of: date, tz: ZoneInfo) -> BillingResult:
    """Everything the run at `as_of` must write for this account. Running it again after
    the result has been stored yields an empty result."""
    _check_stored(state, as_of)
    abrs = tuple(
        abr for product in state.products for abr in _product_abrs(state, product, as_of, tz)
    )
    return BillingResult(abrs=abrs, bills=_bills(state, as_of, abrs))


def _check_stored(state: BillingState, as_of: date) -> None:
    account = state.account
    known = {p.id for p in state.products}
    for abr in state.abrs:
        if abr.currency != account.currency:
            raise InconsistentState(
                f"ABR {abr.id} in {abr.currency}, account in {account.currency}"
            )
        if abr.product_id not in known:
            raise InconsistentState(f"ABR {abr.id} of product {abr.product_id} not in the account")
        if abr.run_date > as_of:
            raise InconsistentState(f"ABR {abr.id} written by the run of {abr.run_date} > {as_of}")
        if abr.period.is_empty or not abr.period.within(
            cycle_period(abr.period.start, account.bill_cycle_day)
        ):
            raise InconsistentState(f"ABR {abr.id} period {abr.period} not inside one cycle period")
    for bill in state.bills:
        if bill.bill_date > as_of:
            raise InconsistentState(f"bill {bill.id} dated {bill.bill_date} > {as_of}")


def _product_abrs(state: BillingState, product: Product, as_of: date, tz: ZoneInfo) -> list[NewAbr]:
    account = state.account
    by_period: dict[date, list[StoredAbr]] = defaultdict(list)
    for abr in state.abrs:
        if abr.product_id == product.id:
            by_period[cycle_period(abr.period.start, account.bill_cycle_day).start].append(abr)

    out: list[NewAbr] = []
    period = cycle_period(product.start_date, account.bill_cycle_day)
    while period.start <= as_of:
        stored = by_period.pop(period.start, [])
        abr = _period_diff(state, product, period, stored, as_of, tz)
        if abr is not None:
            out.append(abr)
        period = DateRange(period.end, add_month(period.end))
    if by_period:
        raise InconsistentState(
            f"product {product.id}: stored ABRs in periods {sorted(by_period)} it is not billed for"
        )
    return out


def _period_diff(
    state: BillingState,
    product: Product,
    period: DateRange,
    stored: list[StoredAbr],
    as_of: date,
    tz: ZoneInfo,
) -> NewAbr | None:
    account = state.account
    rng = billable_range(product, period)
    charges = [a for a in stored if a.kind == "recurring"]
    refunds = [a for a in stored if a.kind == "refund"]
    if len(charges) > 1 or len(refunds) > 1 or (refunds and not charges):
        raise InconsistentState(f"product {product.id}: ABRs of period {period} are {stored}")

    if not charges:
        if rng.is_empty or rng.start > as_of:
            return None
        price = price_at(product, rng.start, state.prices, account.currency, tz)
        return _new_abr(product, "recurring", prorate(price.amount, rng.days, period), account, rng)

    charge = charges[0]
    first_day = max(product.start_date, period.start)
    if charge.period.start != first_day:
        raise InconsistentState(f"charge {charge.id} starts {charge.period.start}, not {first_day}")
    price = price_at(product, charge.period.start, state.prices, account.currency, tz)
    if charge.amount != prorate(price.amount, charge.period.days, period):
        raise InconsistentState(
            f"charge {charge.id} of {charge.amount} does not match price {price.id}"
        )
    actual = charge.amount
    for refund in refunds:
        if refund.refunds_id != charge.id or not refund.period.within(charge.period):
            raise InconsistentState(f"refund {refund.id} does not belong to charge {charge.id}")
        actual += refund.amount

    expected = Decimal("0.00") if rng.is_empty else prorate(price.amount, rng.days, period)
    diff = expected - actual
    if diff == 0:
        return None
    if diff > 0:
        raise InconsistentState(
            f"product {product.id}: period {period} expected {expected} > charged {actual}"
        )
    if refunds:
        raise InconsistentState(f"charge {charge.id} already refunded; one refund per charge")
    unbilled = charge.period if rng.is_empty else DateRange(rng.end, charge.period.end)
    if unbilled.is_empty or not unbilled.within(charge.period):
        raise InconsistentState(f"product {product.id}: refund range {unbilled} for {charge.id}")
    return _new_abr(product, "refund", diff, account, unbilled, refunds_id=charge.id)


def _new_abr(
    product: Product,
    kind: AbrKind,
    amount: Decimal,
    account: Account,
    period: DateRange,
    refunds_id: UUID | None = None,
) -> NewAbr:
    return NewAbr(
        key=f"{kind}:{product.id}:{period.start.isoformat()}",
        product_id=product.id,
        kind=kind,
        amount=amount,
        currency=account.currency,
        period=period,
        refunds_id=refunds_id,
    )


def _bills(state: BillingState, as_of: date, new: tuple[NewAbr, ...]) -> tuple[NewBill, ...]:
    account = state.account
    last = max((b.bill_date for b in state.bills), default=None)
    # (run date, stored id or new key, amount); the ABRs of this run have run date as_of
    pending: list[tuple[date, UUID | str, Decimal]] = []
    for abr in state.abrs:
        if abr.bill_id is None:
            if last is not None and abr.run_date <= last:
                raise InconsistentState(
                    f"ABR {abr.id} of run {abr.run_date} missed the bill of {last}"
                )
            pending.append((abr.run_date, abr.id, abr.amount))
    pending.extend((as_of, abr.key, abr.amount) for abr in new)
    if not pending:
        return ()

    since = min(run_date for run_date, _, _ in pending)
    if last is not None:
        since = max(since, last + timedelta(days=1))
    period = cycle_period(since, account.bill_cycle_day)
    day = period.start if period.start == since else period.end

    bills = []
    while day <= as_of:
        take = [p for p in pending if p[0] <= day]
        pending = [p for p in pending if p[0] > day]
        if take:
            bills.append(
                NewBill(
                    period=DateRange(day, add_month(day)),
                    bill_date=day,
                    amount_due=sum((amount for _, _, amount in take), Decimal("0.00")),
                    currency=account.currency,
                    abr_ids=tuple(ref for _, ref, _ in take if isinstance(ref, UUID)),
                    abr_keys=tuple(ref for _, ref, _ in take if isinstance(ref, str)),
                )
            )
        day = add_month(day)
    return tuple(bills)
