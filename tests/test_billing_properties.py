"""Property tests of the billing core: random cycle days, price histories, products whose
termination becomes known at some point, and sequences of runs with gaps and repeats.

The expected amounts come from an oracle written independently of app/billing: it counts
billable days one by one instead of intersecting ranges."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from app.billing.model import Price, Product
from tests.billing_sim import Ledger, account, midnight, price, product

settings.register_profile(
    "billing",
    max_examples=200,
    deadline=None,
    database=None,  # /app is not writable in the container; failures print their example
    suppress_health_check=[HealthCheck.too_slow],
)
PROFILE = settings.get_profile("billing")

BASE = date(2027, 11, 1)  # the horizon covers Feb 2028 (leap year)
HORIZON = 300
CENT = Decimal("0.01")
# Day distances: half of them short, so terminations often fall into the period already
# charged (which is what produces refunds) and runs are often daily.
SHORT_OR_LONG = st.one_of(st.integers(0, 5), st.integers(0, 45))


@dataclass(frozen=True)
class ProductSpec:
    """A product over time: ordered on `created_on`; its termination (or, before the
    start, cancellation) becomes known on `term_known_on`."""

    name: str
    created_on: date
    start: date
    term_known_on: date | None
    termination: date | None

    @property
    def cancelled(self) -> bool:
        return self.term_known_on is not None and self.term_known_on < self.start

    def visible(self, day: date) -> Product | None:
        if day < self.created_on:
            return None
        if self.term_known_on is not None and self.term_known_on <= day:
            status = "cancelled" if self.cancelled else "terminated"
            return product(self.start, self.termination, status=status, name=self.name)
        return product(self.start, name=self.name)


@dataclass(frozen=True)
class Scenario:
    cycle_day: int
    prices: tuple[Price, ...]
    specs: tuple[ProductSpec, ...]
    runs: tuple[date, ...]

    def products(self, day: date) -> list[Product]:
        return [p for p in (s.visible(day) for s in self.specs) if p is not None]

    def ledger(self) -> Ledger:
        return Ledger(account(self.cycle_day), self.prices)


def day(offset: int) -> date:
    return BASE + timedelta(days=offset)


@st.composite
def product_specs(draw, index: int) -> ProductSpec:
    created = draw(st.integers(0, 120))
    start = created + draw(st.integers(0, 40))
    known = termination = None
    if draw(st.integers(0, 4)) > 0:  # most products get terminated at some point
        # known before the start (a cancellation) up to three months after it; never
        # before the order
        known = max(created, start + draw(st.integers(-15, 90)))
        # deleting a product that has not started cancels it: termination = start.
        # Otherwise the termination date is never in the past when it becomes known.
        termination = start if known < start else known + draw(SHORT_OR_LONG)
    return ProductSpec(
        name=f"p{index}",
        created_on=day(created),
        start=day(start),
        term_known_on=None if known is None else day(known),
        termination=None if termination is None else day(termination),
    )


@st.composite
def scenarios(draw) -> Scenario:
    cycle_day = draw(st.integers(1, 28))
    changes = sorted(draw(st.sets(st.integers(1, HORIZON), max_size=3)))
    cents = draw(
        st.lists(
            st.one_of(st.integers(0, 500), st.integers(0, 10_000_000)),
            min_size=len(changes) + 1,
            max_size=len(changes) + 1,
        )
    )
    bounds = [BASE - timedelta(days=400), *(day(c) for c in changes), None]
    prices = [
        price((Decimal(c) / 100).quantize(CENT), bounds[i], bounds[i + 1], name=f"price{i}")
        for i, c in enumerate(cents)
    ]
    prices.append(price("1.00", currency="USD", name="usd"))  # another currency, never used
    n = draw(st.integers(1, 3))
    specs = tuple(draw(product_specs(i)) for i in range(n))
    # a walk: steps of 0 repeat a run, small steps are daily-ish, large ones are gaps
    runs = [draw(st.integers(0, 60))]
    for step in draw(st.lists(SHORT_OR_LONG, min_size=8, max_size=30)):
        if runs[-1] + step > HORIZON:
            break
        runs.append(runs[-1] + step)
    return Scenario(cycle_day, tuple(prices), specs, tuple(day(r) for r in runs))


# --- oracle, independent of app/billing ---


def period_start(d: date, cycle_day: int) -> date:
    start = date(d.year, d.month, cycle_day)
    if d.day < cycle_day:
        start = (
            date(d.year - 1, 12, cycle_day)
            if d.month == 1
            else date(d.year, d.month - 1, cycle_day)
        )
    return start


def next_period(start: date) -> date:
    return (
        date(start.year + 1, 1, start.day)
        if start.month == 12
        else date(start.year, start.month + 1, start.day)
    )


def oracle_price(prices: tuple[Price, ...], d: date) -> Decimal:
    at = midnight(d)
    [p] = [
        p
        for p in prices
        if p.currency == "EUR" and p.valid_from <= at and (p.valid_to is None or at < p.valid_to)
    ]
    return p.amount


def expected_nets(sc: Scenario, as_of: date) -> dict[tuple[str, date], Decimal]:
    """(product id, period start) -> net for every period whose first billable day <= as_of,
    with the facts known at as_of."""
    out = {}
    for p in sc.products(as_of):
        start = period_start(p.start_date, sc.cycle_day)
        while start <= as_of:
            end = next_period(start)
            days = [
                start + timedelta(days=i)
                for i in range((end - start).days)
                if p.status != "cancelled"
                and p.start_date <= start + timedelta(days=i)
                and (p.termination_date is None or start + timedelta(days=i) < p.termination_date)
            ]
            if days and days[0] <= as_of:
                amount = oracle_price(sc.prices, days[0]) * len(days) / (end - start).days
                out[(str(p.id), start)] = amount.quantize(CENT, rounding=ROUND_HALF_EVEN)
            start = end
    return out


def stored_nets(book: Ledger) -> dict[tuple[str, date], Decimal]:
    nets: dict[tuple[str, date], Decimal] = defaultdict(lambda: Decimal("0.00"))
    for abr in book.abrs:
        nets[
            (str(abr.product_id), period_start(abr.period.start, book.account.bill_cycle_day))
        ] += abr.amount
    return dict(nets)


def run_daily(sc: Scenario) -> Ledger:
    """Apply every run in order, each with the facts known on its date."""
    book = sc.ledger()
    for d in sc.runs:
        book.run(sc.products(d), d)
    return book


# --- properties ---


@settings(PROFILE)
@given(scenarios())
def test_a_second_run_at_the_same_date_emits_nothing(sc):
    book = sc.ledger()
    for d in sc.runs:
        book.run(sc.products(d), d)
        again = book.compute(sc.products(d), d)
        assert again.is_empty, f"second run at {d} emitted {again}"


@settings(PROFILE)
@given(scenarios())
def test_net_per_period_matches_one_rounding_and_lifetime_has_no_drift(sc):
    book = run_daily(sc)
    # coverage of the generator: pytest --hypothesis-show-statistics
    event(f"refunds: {min(len(book.refunds()), 2)}")
    event(f"bills: {min(len(book.bills), 3)}")
    event(f"cancelled: {any(s.cancelled for s in sc.specs)}")
    event(f"prices used: {len({a.amount for a in book.charges() if a.period.days > 27})}")
    last = sc.runs[-1]
    expected = expected_nets(sc, last)
    nets = stored_nets(book)
    # every stored period is expected, and its net is round(price x billable / period days)
    assert {k: v for k, v in nets.items() if k not in expected} == {}
    assert nets == {k: v for k, v in expected.items() if k in nets}
    # every expected period with a non-zero amount is stored (0.00 may be absent: a charge
    # of 0.00 reduced by a termination leaves nothing to refund)
    assert all(k in nets for k, v in expected.items() if v != 0)
    # lifetime total per product: no cent drift across periods
    lifetime = Counter()
    for (pid, _), v in expected.items():
        lifetime[pid] += v
    for spec in sc.specs:
        pid = str(product(spec.start, name=spec.name).id)
        stored = sum((a.amount for a in book.abrs if str(a.product_id) == pid), Decimal(0))
        assert stored == lifetime.get(pid, Decimal(0))


@st.composite
def refund_scenarios(draw) -> Scenario:
    """One product charged and then terminated inside the same period - the case where
    pricing the refund on its own days would drift by a cent. Charges may be partial (the
    product starts mid-period), which is where the drift happens without a rounding tie."""
    cycle_day = draw(st.integers(1, 28))
    cents = draw(st.one_of(st.integers(1, 500), st.integers(1, 10_000_000)))
    first = period_start(day(draw(st.integers(0, HORIZON - 60))), cycle_day)
    end = next_period(first)
    length = (end - first).days
    start = first + timedelta(days=draw(st.integers(0, length - 2)))
    known = start + timedelta(days=draw(st.integers(1, (end - start).days - 1)))
    termination = known + timedelta(days=draw(st.integers(0, (end - known).days - 1)))
    spec = ProductSpec("p0", start, start, known, termination)
    prices = (price((Decimal(cents) / 100).quantize(CENT), name="only"),)
    return Scenario(cycle_day, prices, (spec,), (start, known))


@settings(PROFILE)
@given(refund_scenarios())
def test_refund_keeps_the_period_net_exact(sc):
    book = run_daily(sc)
    [charge] = book.charges()
    refunds = book.refunds()
    # termination > start: the period always has billable days, so it is always expected
    assert stored_nets(book) == expected_nets(sc, sc.runs[-1])
    assert all(r.refunds_id == charge.id for r in refunds)


@settings(PROFILE)
@given(scenarios())
def test_refunds_overlap_precision_currency(sc):
    book = run_daily(sc)
    by_id = {a.id: a for a in book.abrs}
    for refund in book.refunds():
        charge = by_id[refund.refunds_id]
        assert charge.kind == "recurring" and charge.product_id == refund.product_id
        assert refund.amount < 0 and -refund.amount <= charge.amount
        assert refund.period.within(charge.period) and not refund.period.is_empty
    for kind in ("recurring", "refund"):
        rows = [a for a in book.abrs if a.kind == kind]
        for i, a in enumerate(rows):
            for b in rows[i + 1 :]:
                assert not (a.product_id == b.product_id and a.period.overlaps(b.period)), (a, b)
    for abr in book.abrs:
        assert abr.amount == abr.amount.quantize(CENT) and abr.amount.as_tuple().exponent == -2
        assert abr.currency == "EUR"
        assert abr.kind != "recurring" or abr.amount >= 0


@settings(PROFILE)
@given(scenarios())
def test_bills_collect_every_abr_exactly_once(sc):
    book = run_daily(sc)
    if not book.bills:
        assert all(a.bill_id is None for a in book.abrs)
        return
    last = max(b.bill_date for b in book.bills)
    seen = Counter(abr_id for members in book.collected.values() for abr_id in members)
    by_id = {a.id: a for a in book.abrs}
    for abr in book.abrs:
        assert seen[abr.id] == (1 if abr.run_date <= last else 0), abr
    for bill in book.bills:
        members = [by_id[i] for i in book.collected[bill.id]]
        assert members, "an empty bill was issued"
        assert book.amount_due[bill.id] == sum((a.amount for a in members), Decimal(0))
        assert all(a.run_date <= bill.bill_date and a.bill_id == bill.id for a in members)
        assert bill.bill_date.day == sc.cycle_day
        assert (bill.period.start, bill.period.end) == (bill.bill_date, next_period(bill.bill_date))
    dates = sorted(b.bill_date for b in book.bills)
    assert len(dates) == len(set(dates))  # one bill per cycle day: periods never overlap


@settings(PROFILE)
@given(scenarios())
def test_replay_of_the_same_dates_is_identical(sc):
    first, replay = run_daily(sc), run_daily(sc)
    assert first.abrs == replay.abrs
    assert first.bills == replay.bills
    assert first.collected == replay.collected and first.amount_due == replay.amount_due


@settings(PROFILE)
@given(scenarios())
def test_daily_runs_and_one_catch_up_run_give_the_same_net_per_period(sc):
    daily = run_daily(sc)
    last = sc.runs[-1]
    once = sc.ledger()
    once.run(sc.products(last), last)
    # rows may differ (charge + refund vs one reduced charge); a 0.00 charge may exist on
    # one side only, for the same reason
    assert nonzero(stored_nets(daily)) == nonzero(stored_nets(once))


def nonzero(nets: dict[tuple[str, date], Decimal]) -> dict[tuple[str, date], Decimal]:
    return {k: v for k, v in nets.items() if v != 0}
