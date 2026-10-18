"""The billing core: what one account's run at `as_of` must write.

Expected-vs-actual diff. For every product and cycle period whose first billable day is
<= as_of, the expected net is the recurring price x billable days / period days, rounded
once (ROUND_HALF_EVEN). The stored ABRs of that period are compared with it and only the
difference is emitted: a charge when nothing is stored, a refund (expected - charged) when
the expected net dropped because a termination became known later. Pricing the unused
days separately instead would let cent drift accumulate.

Offering changes (docs/decisions/billing-core.md#offering-changes-2026-10-18): the change
days inside a period cut its billable range into segments, each billed like a period -
charged once its first day is <= as_of, at its own offering's price on that day, rounded
once - and matched to its stored charge by that first day. A change seen after the
period was charged refunds the old offering's charge from the change day on.

Taxes (docs/decisions/billing-core.md#taxes-2026-10-18): a charge carries one line per
tax item of its price, the net amount x rate / 100 rounded once, half-even, at rate 0
where the account is exempt from that tax type at midnight of the charge's first day. A
refund's line is the expected tax minus the charge's line, at the charge's rate - the
same diff as the net, so the account's state at refund time never changes a past charge.

Bills: a bill on cycle day D, for [D, next D), collects the unbilled ABRs whose run date is
<= D. A run issues the bills for every cycle day after the last bill up to as_of, skipping
those that would be empty. A bill's amount due includes the taxes of its ABRs.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID
from zoneinfo import ZoneInfo

from app.billing.model import (
    AbrKind,
    Account,
    AppliedTax,
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


def tax_of(amount: Decimal, rate: Decimal) -> Decimal:
    """amount x rate / 100 (a rate in percent), rounded once, half-even, to cents."""
    with localcontext(prec=50):
        exact = amount * rate / 100
    return exact.quantize(CENT, rounding=ROUND_HALF_EVEN)


def midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time(0), tzinfo=tz)


def is_exempt(account: Account, category: str, day: date, tz: ZoneInfo) -> bool:
    at = midnight(day, tz)
    return any(
        e.tax_type == category
        and (e.valid_from is None or e.valid_from <= at)
        and (e.valid_to is None or at < e.valid_to)
        for e in account.exemptions
    )


def charge_taxes(
    net: Decimal, price: Price, account: Account, day: date, tz: ZoneInfo
) -> tuple[AppliedTax, ...]:
    """The tax lines of a charge of `net` first billable on `day`."""
    lines = []
    for tax in price.taxes:
        rate = Decimal(0) if is_exempt(account, tax.category, day, tz) else tax.rate
        lines.append(AppliedTax(tax.category, rate, tax_of(net, rate)))
    return tuple(lines)


def price_at(
    product: Product, day: date, prices: tuple[Price, ...], currency: str, tz: ZoneInfo
) -> Price:
    """The recurring price in `currency` of the product's offering on `day`, valid at
    midnight (tz) of `day`."""
    at = midnight(day, tz)
    offering_id = product.offering_on(day)
    found = [
        p
        for p in prices
        if p.offering_id == offering_id
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
        out += _period_diff(state, product, period, stored, as_of, tz)
        period = DateRange(period.end, add_month(period.end))
    if by_period:
        raise InconsistentState(
            f"product {product.id}: stored ABRs in periods {sorted(by_period)} it is not billed for"
        )
    return out


def segment_starts(product: Product, period: DateRange) -> list[date]:
    """The first days of the period's segments: the product's first day in it, then each
    offering change day inside the period. Each segment is billed on its own."""
    first_day = max(product.start_date, period.start)
    return [first_day, *(c.day for c in product.changes if first_day < c.day < period.end)]


def _period_diff(
    state: BillingState,
    product: Product,
    period: DateRange,
    stored: list[StoredAbr],
    as_of: date,
    tz: ZoneInfo,
) -> list[NewAbr]:
    """The period's billable range cut at the offering change days into segments, each
    billed like a whole period: its charge is matched by its first day."""
    rng = billable_range(product, period)
    charges: dict[date, StoredAbr] = {}
    for charge in (a for a in stored if a.kind == "recurring"):
        if charge.period.start in charges:
            raise InconsistentState(f"product {product.id}: ABRs of period {period} are {stored}")
        charges[charge.period.start] = charge
    refunds: dict[UUID, list[StoredAbr]] = defaultdict(list)
    charge_ids = {c.id for c in charges.values()}
    for refund in (a for a in stored if a.kind == "refund"):
        if refund.refunds_id not in charge_ids:
            raise InconsistentState(
                f"refund {refund.id} does not belong to a charge of period {period}"
            )
        refunds[refund.refunds_id].append(refund)
    starts = segment_starts(product, period)
    if stray := sorted(set(charges) - set(starts)):
        raise InconsistentState(
            f"product {product.id}: charges start {stray}, not on a segment's first day {starts}"
        )
    out = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else period.end
        if rng.is_empty or rng.end <= start:
            seg = DateRange(start, start)
        else:
            seg = DateRange(start, min(end, rng.end))
        charge = charges.get(start)
        abr = _segment_diff(
            state,
            product,
            period,
            start,
            seg,
            charge,
            refunds.get(charge.id, []) if charge else [],
            as_of,
            tz,
        )
        if abr is not None:
            out.append(abr)
    return out


def _segment_diff(
    state: BillingState,
    product: Product,
    period: DateRange,
    start: date,
    rng: DateRange,
    charge: StoredAbr | None,
    refunds: list[StoredAbr],
    as_of: date,
    tz: ZoneInfo,
) -> NewAbr | None:
    """One segment from `start`, billable on `rng` (empty: nothing billable any more)."""
    account = state.account
    offering_id = product.offering_on(start)
    if len(refunds) > 1:
        raise InconsistentState(f"product {product.id}: ABRs of period {period} are {refunds}")

    if charge is None:
        if rng.is_empty or rng.start > as_of:
            return None
        price = price_at(product, rng.start, state.prices, account.currency, tz)
        net = prorate(price.amount, rng.days, period)
        taxes = charge_taxes(net, price, account, rng.start, tz)
        return _new_abr(product, "recurring", net, account, rng, offering_id, taxes=taxes)

    if charge.offering_id is not None and charge.offering_id != offering_id:
        raise InconsistentState(
            f"charge {charge.id} bills offering {charge.offering_id}, the product is on"
            f" {offering_id} from {start}"
        )
    price = price_at(product, charge.period.start, state.prices, account.currency, tz)
    if charge.amount != prorate(price.amount, charge.period.days, period):
        raise InconsistentState(
            f"charge {charge.id} of {charge.amount} does not match price {price.id}"
        )
    _check_charge_taxes(charge, price)
    actual = charge.amount
    for refund in refunds:
        if (
            refund.refunds_id != charge.id
            or not refund.period.within(charge.period)
            or refund.offering_id not in (None, offering_id)
        ):
            raise InconsistentState(f"refund {refund.id} does not belong to charge {charge.id}")
        actual += refund.amount

    expected = Decimal("0.00") if rng.is_empty else prorate(price.amount, rng.days, period)
    diff = expected - actual
    if diff == 0:
        for refund in refunds:
            if refund.taxes != _refund_taxes(charge, expected):
                raise InconsistentState(
                    f"refund {refund.id}: taxes {refund.taxes} do not match charge {charge.id}"
                )
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
    return _new_abr(
        product,
        "refund",
        diff,
        account,
        unbilled,
        offering_id,
        refunds_id=charge.id,
        taxes=_refund_taxes(charge, expected),
    )


def _check_charge_taxes(charge: StoredAbr, price: Price) -> None:
    """A stored charge has one line per tax item of its price, in order, at the price's
    rate or at 0 (the account was exempt when it was charged), each the tax of the
    charge's amount at that rate."""
    if [t.category for t in charge.taxes] != [t.category for t in price.taxes] or any(
        line.rate not in (tax.rate, 0) or line.amount != tax_of(charge.amount, line.rate)
        for line, tax in zip(charge.taxes, price.taxes, strict=True)
    ):
        raise InconsistentState(
            f"charge {charge.id}: taxes {charge.taxes} do not match price {price.id}"
        )


def _refund_taxes(charge: StoredAbr, expected: Decimal) -> tuple[AppliedTax, ...]:
    """Per tax line of the charge: the tax of the expected net minus the tax charged."""
    return tuple(
        AppliedTax(line.category, line.rate, tax_of(expected, line.rate) - line.amount)
        for line in charge.taxes
    )


def _new_abr(
    product: Product,
    kind: AbrKind,
    amount: Decimal,
    account: Account,
    period: DateRange,
    offering_id: UUID,
    refunds_id: UUID | None = None,
    taxes: tuple[AppliedTax, ...] = (),
) -> NewAbr:
    return NewAbr(
        key=f"{kind}:{product.id}:{period.start.isoformat()}",
        product_id=product.id,
        kind=kind,
        amount=amount,
        currency=account.currency,
        period=period,
        refunds_id=refunds_id,
        taxes=taxes,
        offering_id=offering_id,
    )


def _bills(state: BillingState, as_of: date, new: tuple[NewAbr, ...]) -> tuple[NewBill, ...]:
    account = state.account
    last = max((b.bill_date for b in state.bills), default=None)
    # (run date, stored id or new key, amount with taxes); this run's ABRs have run date as_of
    pending: list[tuple[date, UUID | str, Decimal]] = []
    for abr in state.abrs:
        if abr.bill_id is None:
            if last is not None and abr.run_date <= last:
                raise InconsistentState(
                    f"ABR {abr.id} of run {abr.run_date} missed the bill of {last}"
                )
            pending.append((abr.run_date, abr.id, abr.total))
    pending.extend((as_of, abr.key, abr.total) for abr in new)
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
