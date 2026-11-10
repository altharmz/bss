"""Per-account views for the admin UI: the first-charge preview, open products, charges
and the transaction ledger. Every amount here is computed by the backend - by the stage 3
billing core where it is a billing rule - so the UI never re-implements billing math."""

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Request

from app.api import balance, bill, payment
from app.api.common import money, parse_id
from app.api.deps import ClockDep, Tx
from app.api.errors import TmfError, bad_request, not_found, unprocessable
from app.api.inventory import order_href, product_href
from app.api.listing import page_response, parse_page, split_page
from app.api.tmfjson import TmfJSONResponse
from app.billing import entitlement, lifecycle
from app.billing.core import compute, cycle_period, price_at, product_charge_period
from app.billing.engine_tax import TaxUnavailable, quote_taxes
from app.billing.model import Account, BillingState, DateRange, MissingPrice, Price, Product
from app.billing.prepaid import IntervalQuote, fee_quotes
from app.billing.termination import credit
from app.config import settings
from app.internal.context import no_query
from app.network import kind as offering_kind
from app.ordering.cancel import undoable_status_change
from app.repository import billing, catalog, funding, parties, products
from app.repository import lifecycle as lifecycle_repo
from app.repository import payment_methods as payment_methods
from app.repository._sql import Row, fetch_all

BASE = "/internal/v1/billingAccount"
router = APIRouter(prefix=BASE)

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# The product a preview prices: it does not exist, so it has no id of its own.
NO_PRODUCT = UUID(int=0)


def _cadence(price: Price) -> dict[str, str]:
    if (price.recurring_period, price.recurring_period_length) == ("monthly", 1):
        return {}
    units = {"daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"}
    unit = units[price.recurring_period]
    length = price.recurring_period_length
    return {"recurringChargePeriod": unit if length == 1 else f"{length} {unit}s"}


def _params(request: Request, *names: str, optional: tuple[str, ...] = ()) -> dict[str, str]:
    """These query parameters, each once, and the optional ones if given; anything else
    is a 400."""
    for name in request.query_params:
        if name not in names + optional:
            raise bad_request(f"unknown query parameter {name}")
    values = {}
    for name in names + optional:
        given = request.query_params.getlist(name)
        if not given and name in optional:
            continue
        if not given:
            raise bad_request(f"{name} is required")
        if len(given) > 1:
            raise bad_request(f"{name} given more than once")
        values[name] = given[0]
    return values


def _date_param(name: str, raw: str) -> date:
    try:
        if not ISO_DATE.match(raw):
            raise ValueError
        return date.fromisoformat(raw)
    except ValueError:
        raise bad_request(f"{name} must be a date (YYYY-MM-DD)") from None


def _uuid_param(name: str, raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError:
        raise bad_request(f"invalid value for {name}") from None


def _account(conn: psycopg.Connection, account_id: str) -> Row:
    row = parties.get_billing_account(conn, parse_id("billingAccount", account_id))
    if row is None:
        raise not_found("billingAccount", account_id)
    return row


def _core_account(row: Row) -> Account:
    return billing.core_account(row)


def _range(value: DateRange) -> dict[str, date]:
    return {"start": value.start, "end": value.end}


def _first_cycle_day_from(day: date, cycle_day: int) -> date:
    """The first cycle day on or after `day`."""
    period = cycle_period(day, cycle_day)
    return period.start if period.start == day else period.end


def _page(
    request: Request, items: list[dict[str, Any]], more: bool, offset: int, limit: int
) -> TmfJSONResponse:
    return page_response(request, items, more, offset, limit)


@router.get("/{account_id}/firstCharge")
def first_charge(account_id: str, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    """What ordering this offering from `startDate` would charge first, as the billing run
    will write it: the stage 3 core runs on the hypothetical product.

    The charge is written by the run of max(startDate, tomorrow) - the run of D sees
    orders completed before 00:00 of D - and lands on the first bill on or after that run
    date. balanceAfter is today's balance minus this one charge, nothing else.
    """
    params = _params(request, "productOffering.id", "startDate", optional=("product.id",))
    offering_id = _uuid_param("productOffering.id", params["productOffering.id"])
    changed = _uuid_param("product.id", params["product.id"]) if "product.id" in params else None
    start = _date_param("startDate", params["startDate"])
    tz = ZoneInfo(settings.billing_tz)
    today = clock.today()
    with tx() as conn:
        row = _account(conn, account_id)
        offering = catalog.get_product_offering(conn, offering_id)
        if offering is None:
            raise unprocessable(f"productOffering {offering_id} does not exist")
        if start < today:
            raise unprocessable(f"startDate {start} is in the past")
        account = _core_account(row)
        product = Product(
            id=NO_PRODUCT,
            offering_id=offering_id,
            status="created",
            start_date=start,
            termination_date=None,
        )
        state = BillingState(
            account=account,
            products=(product,),
            prices=billing.load_recurring_prices(conn, [offering_id]),
            abrs=(),
            bills=(),
        )
        if row["rating_type"] == "prepaid":
            try:
                price = price_at(product, start, state.prices, account.currency, tz)
            except MissingPrice:
                if offering_kind.kind_of(offering["details"]) != offering_kind.PLAN:
                    return _fees_first_charge(conn, row, offering, start, account, today)
                raise unprocessable(
                    f"productOffering {offering['name']} has no recurring price in"
                    f" {account.currency} valid on {start}"
                ) from None
            return _prepaid_first_charge(conn, row, offering, start, price, account, today, changed)
        try:
            [charge] = compute(state, start, tz).abrs
            price = price_at(product, start, state.prices, account.currency, tz)
        except MissingPrice:
            raise unprocessable(
                f"productOffering {offering['name']} has no recurring price in"
                f" {account.currency} valid on {start}"
            ) from None
        try:
            following = compute(state, charge.period.end, tz).abrs[1]
        except MissingPrice:  # the price ends with this period and nothing follows it
            following = None
        balance = billing.account_balance(conn, row["id"])
    cycle = product_charge_period(
        product, start, state.prices, account.currency, account.bill_cycle_day, tz
    )
    run_date = max(start, today + timedelta(days=1))
    after = balance - charge.total
    limit = row["credit_limit"]
    return TmfJSONResponse(
        {
            "billingAccount": {"id": str(row["id"])},
            "productOffering": {"id": str(offering_id), "name": offering["name"]},
            "startDate": start,
            "period": _range(charge.period),
            "cyclePeriod": _range(cycle),
            "billableDays": charge.period.days,
            "periodDays": cycle.days,
            "prorated": charge.period.days < cycle.days,
            "price": money(price.amount, price.currency),
            **_cadence(price),
            "amount": money(charge.total, charge.currency),
            "chargeRunDate": run_date,
            "billDate": _first_cycle_day_from(run_date, account.bill_cycle_day),
            "next": None
            if following is None
            else {
                "start": following.period.start,
                "amount": money(following.total, following.currency),
            },
            "balance": money(balance, account.currency),
            "balanceAfter": money(after, account.currency),
            "creditLimit": None if limit is None else money(limit, account.currency),
            "exceedsCreditLimit": limit is not None and after < -limit,
        }
    )


def _prepaid_first_charge(
    conn: psycopg.Connection,
    row: Row,
    offering: Row,
    start: date,
    price: Price,
    account: Account,
    today: date,
    changed: UUID | None = None,
) -> TmfJSONResponse:
    """A prepaid purchase pays its first interval in full, tax included, from the funds
    left after holds, and the interval starts on the start date (anniversary periods:
    nothing is prorated). `required`, `available` and `shortfall` are the extra
    fields; the postpaid ones keep their meaning with `balanceAfter` = ledger minus the
    interval. A start after today holds nothing at order time; the figures then show
    today's funds."""
    tz = ZoneInfo(settings.billing_tz)
    try:
        taxes, tax_quote_id = quote_taxes(conn, account, row["id"], price, price.amount, start, tz)
    except TaxUnavailable as error:
        raise TmfError(503, f"tax engine unavailable: {error}") from None
    quote = IntervalQuote(
        product_id=NO_PRODUCT,
        offering_id=price.offering_id,
        price_id=price.id,
        currency=account.currency,
        amount=price.amount,
        taxes=taxes,
        tax_quote_id=tax_quote_id,
        anchor=start,
        index=0,
        unit=price.recurring_period,
        length=price.recurring_period_length,
    )
    period = quote.period()
    required = price.amount + sum((t.amount for t in taxes), Decimal(0))
    snapshot = funding.load(conn, row["id"])
    available = max(Decimal(0), snapshot.spendable)
    shortfall = max(Decimal(0), required - available)
    currency = account.currency
    run_date = max(start, today)
    unused = None if changed is None else _unused_credit(conn, row["id"], changed, start)
    fees = _quote_fees(conn, account, offering["id"], start, tz) if changed is None else []
    required += sum((f.gross for f in fees), Decimal(0))
    shortfall = max(Decimal(0), required - available)
    return TmfJSONResponse(
        {
            "billingAccount": {"id": str(row["id"])},
            "productOffering": {"id": str(offering["id"]), "name": offering["name"]},
            "startDate": start,
            "period": _range(period),
            "cyclePeriod": _range(period),
            "billableDays": period.days,
            "periodDays": period.days,
            "prorated": False,
            "price": money(price.amount, price.currency),
            **_cadence(price),
            "amount": money(required, currency),
            "chargeRunDate": run_date,
            "billDate": run_date,
            "next": None,
            "balance": money(snapshot.ledger, currency),
            "balanceAfter": money(snapshot.ledger - required + (unused or 0), currency),
            "creditLimit": None,
            "exceedsCreditLimit": shortfall > 0,
            "required": money(required, currency),
            "available": money(available, currency),
            "shortfall": money(shortfall, currency),
            **({} if unused is None else {"credit": money(unused, currency)}),
            **({"fees": _fee_lines(fees, currency)} if fees else {}),
        }
    )


def _quote_fees(conn, account: Account, offering_id: UUID, start: date, tz: ZoneInfo):
    try:
        return fee_quotes(conn, account, offering_id, start, tz)
    except TaxUnavailable as error:
        raise TmfError(503, f"tax engine unavailable: {error}") from None


def _fee_lines(fees, currency: str) -> list[dict[str, Any]]:
    return [
        {
            "name": f.name,
            "amount": money(f.amount, currency),
            "tax": money(f.gross - f.amount, currency),
            "gross": money(f.gross, currency),
        }
        for f in fees
    ]


def _fees_first_charge(
    conn, row: Row, offering: Row, start: date, account: Account, today: date
) -> TmfJSONResponse:
    """An add-on or pay-as-you-go offering has no recurring price: its purchase charges its
    one-time fees, held now (they start today). The one-day period is the fee's own."""
    tz = ZoneInfo(settings.billing_tz)
    fees = _quote_fees(conn, account, offering["id"], start, tz)
    if not fees:
        raise unprocessable(
            f"productOffering {offering['name']} has no one-time price in"
            f" {account.currency} valid on {start}"
        )
    required = sum((f.gross for f in fees), Decimal(0))
    snapshot = funding.load(conn, row["id"])
    available = max(Decimal(0), snapshot.spendable)
    shortfall = max(Decimal(0), required - available)
    currency = account.currency
    period = DateRange(start, start + timedelta(days=1))
    run_date = max(start, today)
    return TmfJSONResponse(
        {
            "billingAccount": {"id": str(row["id"])},
            "productOffering": {"id": str(offering["id"]), "name": offering["name"]},
            "startDate": start,
            "period": _range(period),
            "cyclePeriod": _range(period),
            "billableDays": 1,
            "periodDays": 1,
            "prorated": False,
            "price": money(Decimal(0), currency),
            "amount": money(required, currency),
            "chargeRunDate": run_date,
            "billDate": run_date,
            "next": None,
            "balance": money(snapshot.ledger, currency),
            "balanceAfter": money(snapshot.ledger - required, currency),
            "creditLimit": None,
            "exceedsCreditLimit": shortfall > 0,
            "required": money(required, currency),
            "available": money(available, currency),
            "shortfall": money(shortfall, currency),
            "fees": _fee_lines(fees, currency),
        }
    )


def _unused_credit(conn, account_id, product_id, start: date) -> Decimal:
    """What changing this prepaid product's offering on `start` credits for the unused
    dates of its paid interval, tax included (a termination credit's arithmetic); zero at
    or after its paid-through date. The credit funds nothing at acceptance."""
    product = products.get_product(conn, product_id)
    if product is None or product["billing_account_id"] != account_id:
        raise unprocessable(f"product {product_id} does not belong to this billing account")
    grant = entitlement.latest_grant(conn, product_id)
    if grant is None or not grant["period"].lower <= start < grant["period"].upper:
        return Decimal(0)
    found = credit(
        billing.get_abr(conn, grant["applied_rate_id"]),
        DateRange(grant["period"].lower, grant["period"].upper),
        start,
    )
    if found is None:
        return Decimal(0)
    net, taxes = found
    return -(net + sum((t.amount for t in taxes), Decimal(0)))


def _next_charge(state: BillingState, today: date, tz: ZoneInfo) -> date | None:
    """First future run with a recurring charge, using the worker's due calculation.

    Tomorrow sees today's completed orders. Stored charges distinguish an unpaid first
    period from an already charged one; status alone never decides billing eligibility.
    """
    product = state.products[0]
    tomorrow = today + timedelta(days=1)
    first = max(tomorrow, product.start_date)
    boundary = product_charge_period(
        product,
        max(today, product.start_date),
        state.prices,
        state.account.currency,
        state.account.bill_cycle_day,
        tz,
    ).end
    candidates = sorted(
        {first, max(tomorrow, boundary), *(c.day for c in product.changes if c.day > today)}
    )
    for day in candidates:
        if any(abr.kind == "recurring" for abr in compute(state, day, tz).abrs):
            return day
    return None


def _renewal_history(conn: psycopg.Connection, product_ids: list[UUID]) -> dict[UUID, list]:
    """Each prepaid product's funded intervals, newest first: what bought or renewed it,
    for which dates, how much and whether the card paid the shortfall."""
    rows = fetch_all(
        conn,
        "SELECT f.product_id, f.authorized_at, f.period, f.amount, f.tax_amount, f.command_kind,"
        " f.supersedes_interval_id IS NOT NULL AS superseding, a.customer_bill_id AS bill_id,"
        " a.billing_account_id, EXISTS (SELECT FROM payment_command c WHERE c.state = 'confirmed'"
        "  AND c.renewal_command_id = f.command_id) AS by_card, f.currency"
        " FROM funded_interval f JOIN applied_customer_billing_rate a ON a.id = f.applied_rate_id"
        " WHERE f.product_id = ANY(%s) ORDER BY lower(f.period) DESC, f.authorized_at DESC",
        (product_ids,),
    )
    out: dict[UUID, list] = {}
    for r in rows:
        out.setdefault(r["product_id"], []).append(
            {
                "authorizedAt": r["authorized_at"],
                "period": {"start": r["period"].lower, "end": r["period"].upper},
                "kind": "renewal"
                if r["command_kind"] == "renewal"
                else "offeringChange"
                if r["superseding"]
                else "purchase",
                "amount": money(r["amount"] + r["tax_amount"], r["currency"]),
                "tax": money(r["tax_amount"], r["currency"]),
                "fundedBy": "card" if r["by_card"] else "balance",
                "bill": None
                if r["bill_id"] is None
                else {"id": str(r["bill_id"]), "href": bill.bill_href(r["bill_id"])},
            }
        )
    return out


def _pending_change(c: Row) -> dict[str, Any]:
    """One `pendingChanges` entry: a status or offering change not yet in effect."""
    out: dict[str, Any] = {
        "kind": c["kind"],
        "date": c["date"],
        "productOrder": {"id": str(c["order_id"]), "href": order_href(c["order_id"])},
    }
    if c["kind"] == "status":
        out["status"] = c["status"]
    else:
        out["productOffering"] = {"id": str(c["offering_id"]), "name": c["offering_name"]}
    return out


@router.get("/{account_id}/products")
def open_products(account_id: str, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    """The account's products that are not ended (created, pendingActive, active,
    pendingTerminate, suspended), oldest start first. `price` is the recurring price in
    the account's currency valid today, or on the start date for a product that has not
    started; null if there is none. `undoableStatusChange` is an applied status change
    today whose completed order can still be cancelled; null otherwise. Paged with
    offset/limit like every list (#312)."""
    offset, limit = parse_page(request)
    tz = ZoneInfo(settings.billing_tz)
    today = clock.today()
    with tx() as conn:
        row = _account(conn, account_id)
        account = _core_account(row)
        rows, more = split_page(products.list_open_products(conn, row["id"], today)[offset:], limit)
        prepaid_views = (
            {p["id"]: entitlement.view(conn, p, row, today) for p in rows}
            if row["rating_type"] == "prepaid"
            else {}
        )
        lifecycles = (
            {
                p["id"]: lifecycle.view(conn, p, row, lifecycle_repo.settings(conn), today)
                for p in rows
            }
            if row["rating_type"] == "prepaid"
            else {}
        )
        changes = billing.offering_changes(conn, [p["id"] for p in rows])
        pending = products.pending_changes(conn, [p["id"] for p in rows], today)
        undoable = {p["id"]: undoable_status_change(conn, p, today) for p in rows}
        history = (
            _renewal_history(conn, [p["id"] for p in rows])
            if row["rating_type"] == "prepaid"
            else {}
        )
        card = (
            row["rating_type"] == "prepaid"
            and row["auto_topup_enabled"]
            and payment_methods.default_of(conn, row["id"]) is not None
        )
        cores = {p["id"]: billing.core_product(p, changes.get(p["id"], ())) for p in rows}
        prices = billing.load_recurring_prices(
            conn, [o for p in cores.values() for o in p.offering_ids]
        )
        stored = billing.load_abrs(conn, row["id"]) if account.rating_type == "postpaid" else ()
    items = []
    for p in rows:
        core = cores[p["id"]]
        nxt = None
        try:
            price = price_at(core, max(today, p["start_date"]), prices, account.currency, tz)
        except MissingPrice:
            price = None
        if account.rating_type != "prepaid":
            try:
                nxt = _next_charge(
                    BillingState(
                        account=account,
                        products=(core,),
                        prices=prices,
                        abrs=tuple(a for a in stored if a.product_id == core.id),
                        bills=(),
                    ),
                    today,
                    tz,
                )
            except MissingPrice:
                pass  # a missing future price must not hide today's price
        items.append(
            {
                "id": str(p["id"]),
                "href": product_href(p["id"]),
                "status": p["status"],
                "startDate": p["start_date"],
                "terminationDate": p["termination_date"],
                "productOffering": {
                    "id": str(p["offering_id"]),
                    "name": p["offering_name"],
                    "code": p["offering_code"],
                },
                "productSpecification": None  # an offering without a specification
                if p["specification_id"] is None
                else {"id": str(p["specification_id"]), "name": p["specification_name"]},
                "price": None if price is None else money(price.amount, price.currency),
                **({} if price is None else _cadence(price)),
                "nextChargeDate": prepaid_views[p["id"]]["paidThrough"]
                if p["id"] in prepaid_views
                else nxt,
                "undoableStatusChange": undoable[p["id"]],
                "pendingChanges": [_pending_change(c) for c in pending.get(p["id"], [])],
                **prepaid_views.get(p["id"], {}),
                **(
                    {
                        "offeringKind": offering_kind.kind_of(p["offering_details"]),
                        "parentProduct": None
                        if p["parent_product_id"] is None
                        else {
                            "id": str(p["parent_product_id"]),
                            "href": product_href(p["parent_product_id"]),
                        },
                        "fundingSource": "balanceThenCard" if card else "balance",
                        "renewalHistory": history.get(p["id"], []),
                        "lifecycle": lifecycles[p["id"]],
                    }
                    if p["id"] in prepaid_views
                    else {}
                ),
            }
        )
    return _page(request, items, more, offset, limit)


@router.get("/{account_id}/settlement")
def account_settlement(
    account_id: str, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """What the account owes on its bills and the credit nothing has used yet (design 6.5).
    outstanding includes bills on hold, which are also counted apart; overdue = due before
    the business date with something remaining - display only, nothing follows from it."""
    no_query(request)
    with tx() as conn:
        row = _account(conn, account_id)
        s = billing.settlement_summary(conn, row["id"], clock.today())
    currency = row["currency"]
    return TmfJSONResponse(
        {
            "outstanding": money(s["outstanding"], currency),
            "unallocatedCredit": money(s["unallocated_credit"], currency),
            "overdue": {
                "count": s["overdue_count"],
                "amount": money(s["overdue_amount"], currency),
            },
            "onHold": {
                "count": s["on_hold_count"],
                "amount": money(s["on_hold_amount"], currency),
            },
            "paymentTermDays": settings.bill_payment_term_days,
        }
    )


@router.get("/{account_id}/charges")
def charges(account_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    """The account's ABRs (charges and refunds), newest run first. billableDays of
    periodDays is the proration: the days the ABR covers out of its cycle period."""
    offset, limit = parse_page(request)
    with tx() as conn:
        row = _account(conn, account_id)
        rows, more = billing.page_account_charges(conn, row["id"], offset=offset, limit=limit)
        periods = {}
        for a in rows:
            if a["product_id"] is None:
                continue  # a transfer fee covers its one day: no cycle to prorate against
            product = products.get_product(conn, a["product_id"])
            assert product is not None
            periods[a["id"]] = billing.product_period(
                conn,
                product | {"currency": row["currency"]},
                a["charge_start"],
                row["bill_cycle_day"],
            )
    items = []
    for a in rows:
        covered = DateRange(a["period"].lower, a["period"].upper)
        cycle = periods.get(a["id"], covered)
        items.append(
            {
                "id": str(a["id"]),
                "href": f"{bill.BASE}/appliedCustomerBillingRate/{a['id']}",
                "kind": "refund" if a["charge_type"] == "refund" else "charge",
                "runDate": a["run_date"],
                "period": _range(covered),
                "billableDays": covered.days,
                "periodDays": cycle.days,
                "prorated": covered.days < cycle.days,
                "amount": money(billing.abr_total(a), a["currency"]),
                "product": None
                if a["product_id"] is None
                else {"id": str(a["product_id"]), "href": product_href(a["product_id"])},
                "productOffering": None
                if a["offering_id"] is None
                else {"id": str(a["offering_id"]), "name": a["offering_name"]},
                "bill": None
                if a["customer_bill_id"] is None
                else {
                    "id": str(a["customer_bill_id"]),
                    "href": bill.bill_href(a["customer_bill_id"]),
                },
            }
        )
    return _page(request, items, more, offset, limit)


def _transaction_href(t: Row) -> str:
    if t["kind"] == "payment":
        return f"{payment.BASE}/payment/{t['id']}"
    if t["kind"] in ("adjustment", "reversal", "transferIn", "transferOut"):
        return f"{balance.BASE}/balanceActionHistory/{t['id']}"
    return f"{bill.BASE}/appliedCustomerBillingRate/{t['id']}"


@router.get("/{account_id}/transactions")
def transactions(account_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    """Charges, refunds, payments and, on a prepaid account, balance actions with the balance
    after each, newest first. Positive = credit to the customer, as in accountBalance; the
    newest balanceAfter is the account's balance."""
    offset, limit = parse_page(request)
    with tx() as conn:
        row = _account(conn, account_id)
        rows, more = billing.page_account_transactions(
            conn, row["id"], tz=settings.billing_tz, offset=offset, limit=limit
        )
    currency = row["currency"]
    items = [
        {
            "id": str(t["id"]),
            "href": _transaction_href(t),
            "type": t["kind"],
            "date": t["day"],
            "note": t["note"],
            "period": None
            if t["period"] is None
            else {"start": t["period"].lower, "end": t["period"].upper},
            "amount": money(t["amount"], currency),
            "balanceAfter": money(t["balance_after"], currency),
        }
        for t in rows
    ]
    return _page(request, items, more, offset, limit)
