"""Per-account views for the admin UI: the first-charge preview, open products, charges
and the transaction ledger. Every amount here is computed by the backend - by the stage 3
billing core where it is a billing rule - so the UI never re-implements billing math."""

import re
from datetime import date, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Request

from app.api import bill, payment
from app.api.common import money, parse_id
from app.api.deps import ClockDep, Tx
from app.api.errors import bad_request, not_found, unprocessable
from app.api.inventory import product_href
from app.api.listing import parse_page
from app.api.tmfjson import TmfJSONResponse
from app.billing import entitlement
from app.billing.core import compute, cycle_period, price_at, product_charge_period
from app.billing.model import Account, BillingState, DateRange, MissingPrice, Price, Product
from app.config import settings
from app.internal.context import no_query
from app.repository import billing, catalog, parties, products
from app.repository._sql import Row

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


def _params(request: Request, *names: str) -> dict[str, str]:
    """Exactly these query parameters, each once; anything else is a 400."""
    for name in request.query_params:
        if name not in names:
            raise bad_request(f"unknown query parameter {name}")
    values = {}
    for name in names:
        given = request.query_params.getlist(name)
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


def _page(items: list[dict[str, Any]], total: int) -> TmfJSONResponse:
    return TmfJSONResponse(
        items, headers={"X-Total-Count": str(total), "X-Result-Count": str(len(items))}
    )


@router.get("/{account_id}/firstCharge")
def first_charge(account_id: str, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    """What ordering this offering from `startDate` would charge first, as the billing run
    will write it: the stage 3 core runs on the hypothetical product.

    The charge is written by the run of max(startDate, tomorrow) - the run of D sees
    orders completed before 00:00 of D - and lands on the first bill on or after that run
    date. balanceAfter is today's balance minus this one charge, nothing else.
    """
    params = _params(request, "productOffering.id", "startDate")
    offering_id = _uuid_param("productOffering.id", params["productOffering.id"])
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


def _next_charge(product: Row, today: date, cycle_day: int) -> date | None:
    """The first day of the next period to be charged: the start date while the product
    has not started, otherwise the next cycle day after today - or none if the product
    ends by then."""
    if product["start_date"] > today:
        nxt = product["start_date"]
    else:
        nxt = cycle_period(today, cycle_day).end
    end = product["termination_date"]
    return None if end is not None and end <= nxt else nxt


@router.get("/{account_id}/products")
def open_products(account_id: str, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    """The account's products that are not ended (created, pendingActive, active,
    pendingTerminate, suspended), oldest start first. `price` is the recurring price in
    the account's currency valid today, or on the start date for a product that has not
    started; null if there is none."""
    _params(request)
    tz = ZoneInfo(settings.billing_tz)
    today = clock.today()
    with tx() as conn:
        row = _account(conn, account_id)
        account = _core_account(row)
        rows = products.list_open_products(conn, row["id"], today)
        prepaid_views = (
            {p["id"]: entitlement.view(conn, p, row, today) for p in rows}
            if row["rating_type"] == "prepaid"
            else {}
        )
        changes = billing.offering_changes(conn, [p["id"] for p in rows])
        cores = {p["id"]: billing.core_product(p, changes.get(p["id"], ())) for p in rows}
        prices = billing.load_recurring_prices(
            conn, [o for p in cores.values() for o in p.offering_ids]
        )
    items = []
    for p in rows:
        core = cores[p["id"]]
        nxt = _next_charge(p, today, account.bill_cycle_day)
        try:
            price = price_at(core, max(today, p["start_date"]), prices, account.currency, tz)
            if p["start_date"] <= today:
                nxt = product_charge_period(
                    core, today, prices, account.currency, account.bill_cycle_day, tz
                ).end
                nxt = min([nxt, *(c.day for c in core.changes if c.day > today)])
                if p["termination_date"] is not None and p["termination_date"] <= nxt:
                    nxt = None
        except MissingPrice:
            price = None
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
                **prepaid_views.get(p["id"], {}),
            }
        )
    return _page(items, len(items))


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
        rows, total = billing.page_account_charges(conn, row["id"], offset=offset, limit=limit)
        periods = {}
        for a in rows:
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
        cycle = periods[a["id"]]
        items.append(
            {
                "id": str(a["id"]),
                "href": f"{bill.BASE}/appliedCustomerBillingRate/{a['id']}",
                "kind": "charge" if a["charge_type"] == "recurring" else "refund",
                "runDate": a["run_date"],
                "period": _range(covered),
                "billableDays": covered.days,
                "periodDays": cycle.days,
                "prorated": covered.days < cycle.days,
                "amount": money(billing.abr_total(a), a["currency"]),
                "product": {"id": str(a["product_id"]), "href": product_href(a["product_id"])},
                "productOffering": {"id": str(a["offering_id"]), "name": a["offering_name"]},
                "bill": None
                if a["customer_bill_id"] is None
                else {
                    "id": str(a["customer_bill_id"]),
                    "href": bill.bill_href(a["customer_bill_id"]),
                },
            }
        )
    return _page(items, total)


@router.get("/{account_id}/transactions")
def transactions(account_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    """Charges, refunds and payments with the balance after each, newest first. Positive
    = credit to the customer, as in accountBalance; the newest balanceAfter is the
    account's balance."""
    offset, limit = parse_page(request)
    with tx() as conn:
        row = _account(conn, account_id)
        rows, total = billing.page_account_transactions(
            conn, row["id"], tz=settings.billing_tz, offset=offset, limit=limit
        )
    currency = row["currency"]
    items = [
        {
            "id": str(t["id"]),
            "href": f"{payment.BASE}/payment/{t['id']}"
            if t["kind"] == "payment"
            else f"{bill.BASE}/appliedCustomerBillingRate/{t['id']}",
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
    return _page(items, total)
