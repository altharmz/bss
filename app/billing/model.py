"""Inputs and outputs of the billing core. Frozen dataclasses only: the repository maps
rows to these at the boundary, the core never sees a dict or a connection."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

AbrKind = Literal["recurring", "refund"]


class BillingError(Exception):
    """The account's state cannot be billed. Nothing is written for it."""


class MissingPrice(BillingError):
    def __init__(self, product_id: UUID, day: date, currency: str) -> None:
        super().__init__(
            f"product {product_id}: no recurring price in {currency} valid at midnight of {day}"
        )
        self.product_id = product_id
        self.day = day
        self.currency = currency


class InconsistentState(BillingError):
    """Stored ABRs or bills contradict the billing rules (docs/decisions/billing-core.md)."""


@dataclass(frozen=True)
class DateRange:
    """Half-open [start, end), like Postgres `daterange`. May be empty (start == end)."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"range end {self.end} before start {self.start}")

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    @property
    def is_empty(self) -> bool:
        return self.start == self.end

    def within(self, other: "DateRange") -> bool:
        return other.start <= self.start and self.end <= other.end

    def overlaps(self, other: "DateRange") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class Exemption:
    """A full exemption from the taxes of one type (TMF666 taxExemption: a certificate's
    taxDefinition[].taxType), valid in [valid_from, valid_to); None = unbounded."""

    tax_type: str
    valid_from: datetime | None
    valid_to: datetime | None


@dataclass(frozen=True)
class Account:
    id: UUID
    currency: str
    bill_cycle_day: int
    exemptions: tuple[Exemption, ...] = ()


@dataclass(frozen=True)
class Tax:
    """One tax item of a price (TMF620 TaxItem): a category and a rate in percent."""

    category: str
    rate: Decimal


@dataclass(frozen=True)
class AppliedTax:
    """One tax line of an ABR (TMF678 AppliedBillingTaxRate). `rate` is the rate applied
    (0 when the account is exempt), `amount` the tax in the ABR's currency."""

    category: str
    rate: Decimal
    amount: Decimal


@dataclass(frozen=True)
class Price:
    """A recurring monthly price (length 1). `valid_to` is exclusive, None = open-ended.
    `amount` is before tax; `taxes` are applied on top of it."""

    id: UUID
    offering_id: UUID
    amount: Decimal
    currency: str
    valid_from: datetime
    valid_to: datetime | None
    taxes: tuple[Tax, ...] = ()


@dataclass(frozen=True)
class OfferingChange:
    """A modify item's offering change: the product is on `offering_id` from `day` on."""

    day: date
    offering_id: UUID


@dataclass(frozen=True)
class Product:
    id: UUID
    offering_id: UUID  # the offering it was created with
    status: str
    start_date: date
    termination_date: date | None  # exclusive: the first day not billed
    changes: tuple[OfferingChange, ...] = ()  # sorted by day, each day once

    def __post_init__(self) -> None:
        days = [c.day for c in self.changes]
        if days != sorted(set(days)):
            raise ValueError(f"product {self.id}: offering changes not sorted by distinct day")

    def offering_on(self, day: date) -> UUID:
        """The offering on `day`: the latest change on or before it, else the initial one."""
        offering = self.offering_id
        for change in self.changes:
            if change.day > day:
                break
            offering = change.offering_id
        return offering

    @property
    def offering_ids(self) -> tuple[UUID, ...]:
        """Every offering the product has had or will have."""
        return (self.offering_id, *(c.offering_id for c in self.changes))


@dataclass(frozen=True)
class StoredAbr:
    id: UUID
    product_id: UUID
    kind: AbrKind
    amount: Decimal
    currency: str
    period: DateRange
    refunds_id: UUID | None
    bill_id: UUID | None
    run_date: date  # billing_run.run_date of the run that wrote it
    taxes: tuple[AppliedTax, ...] = ()
    offering_id: UUID | None = None  # the offering it bills; None: not checked

    @property
    def total(self) -> Decimal:
        """What the ABR adds to the account's debt: the amount with its taxes."""
        return self.amount + sum((t.amount for t in self.taxes), Decimal("0.00"))


@dataclass(frozen=True)
class StoredBill:
    id: UUID
    period: DateRange
    bill_date: date


@dataclass(frozen=True)
class BillingState:
    """Everything stored for one billing account."""

    account: Account
    products: tuple[Product, ...]
    prices: tuple[Price, ...]  # recurring prices of the products' offerings, any currency
    abrs: tuple[StoredAbr, ...]
    bills: tuple[StoredBill, ...]


@dataclass(frozen=True)
class NewAbr:
    """An ABR to insert. `key` identifies it within one result (bills refer to it)."""

    key: str
    product_id: UUID
    kind: AbrKind
    amount: Decimal
    currency: str
    period: DateRange
    refunds_id: UUID | None  # set on refunds: always a stored charge
    taxes: tuple[AppliedTax, ...] = ()
    offering_id: UUID | None = None  # the offering it bills (a refund: its charge's)

    @property
    def total(self) -> Decimal:
        return self.amount + sum((t.amount for t in self.taxes), Decimal("0.00"))


@dataclass(frozen=True)
class NewBill:
    period: DateRange
    bill_date: date
    amount_due: Decimal  # with taxes
    currency: str
    abr_ids: tuple[UUID, ...]  # stored ABRs it collects
    abr_keys: tuple[str, ...]  # ABRs of this result it collects


@dataclass(frozen=True)
class BillingResult:
    abrs: tuple[NewAbr, ...]
    bills: tuple[NewBill, ...]

    @property
    def is_empty(self) -> bool:
        return not self.abrs and not self.bills
