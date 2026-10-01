"""Builders for the billing core and an in-memory ledger that stores results the way the
DB adapter does: ABRs get ids and their run date, bills collect them."""

import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.billing.core import compute
from app.billing.model import (
    Account,
    BillingResult,
    BillingState,
    DateRange,
    Price,
    Product,
    StoredAbr,
    StoredBill,
)

TZ = ZoneInfo("Europe/Moscow")
NS = uuid.UUID("6c1d1c55-0000-4000-8000-000000000000")
OFFERING = uuid.uuid5(NS, "offering")


def uid(name: str) -> uuid.UUID:
    return uuid.uuid5(NS, name)


def midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=TZ)


def account(cycle_day: int = 1, currency: str = "EUR") -> Account:
    return Account(id=uid("account"), currency=currency, bill_cycle_day=cycle_day)


def price(
    amount: str | Decimal,
    valid_from: date | datetime = date(2020, 1, 1),
    valid_to: date | datetime | None = None,
    *,
    currency: str = "EUR",
    name: str | None = None,
) -> Price:
    def instant(value: date | datetime) -> datetime:
        return value if isinstance(value, datetime) else midnight(value)

    return Price(
        id=uid(name or f"price:{amount}:{valid_from}:{currency}"),
        offering_id=OFFERING,
        amount=Decimal(amount),
        currency=currency,
        valid_from=instant(valid_from),
        valid_to=None if valid_to is None else instant(valid_to),
    )


def product(
    start: date,
    termination: date | None = None,
    *,
    status: str = "active",
    name: str = "p1",
) -> Product:
    return Product(
        id=uid(name),
        offering_id=OFFERING,
        status=status,
        start_date=start,
        termination_date=termination,
    )


@dataclass
class Ledger:
    """One account's stored ABRs and bills, updated by applying each run's result."""

    account: Account
    prices: tuple[Price, ...]
    abrs: list[StoredAbr] = field(default_factory=list)
    bills: list[StoredBill] = field(default_factory=list)
    # bill id -> ABR ids it collected, as reported by the core
    collected: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=dict)
    amount_due: dict[uuid.UUID, Decimal] = field(default_factory=dict)

    def state(self, products: list[Product]) -> BillingState:
        return BillingState(
            account=self.account,
            products=tuple(products),
            prices=self.prices,
            abrs=tuple(self.abrs),
            bills=tuple(self.bills),
        )

    def compute(self, products: list[Product], as_of: date) -> BillingResult:
        return compute(self.state(products), as_of, TZ)

    def run(self, products: list[Product], as_of: date) -> BillingResult:
        result = self.compute(products, as_of)
        self.apply(result, as_of)
        return result

    def apply(self, result: BillingResult, run_date: date) -> None:
        ids = {}
        for new in result.abrs:
            ids[new.key] = uid(f"abr:{run_date}:{new.key}")
            self.abrs.append(
                StoredAbr(
                    id=ids[new.key],
                    product_id=new.product_id,
                    kind=new.kind,
                    amount=new.amount,
                    currency=new.currency,
                    period=new.period,
                    refunds_id=new.refunds_id,
                    bill_id=None,
                    run_date=run_date,
                )
            )
        for bill in result.bills:
            bill_id = uid(f"bill:{bill.bill_date}")
            self.bills.append(StoredBill(id=bill_id, period=bill.period, bill_date=bill.bill_date))
            members = [*bill.abr_ids, *(ids[key] for key in bill.abr_keys)]
            self.collected[bill_id] = members
            self.amount_due[bill_id] = bill.amount_due
            self.abrs = [replace(a, bill_id=bill_id) if a.id in members else a for a in self.abrs]

    def charges(self) -> list[StoredAbr]:
        return [a for a in self.abrs if a.kind == "recurring"]

    def refunds(self) -> list[StoredAbr]:
        return [a for a in self.abrs if a.kind == "refund"]


def rng(start: date, end: date) -> DateRange:
    return DateRange(start, end)
