"""The internal API for the admin UI (/internal/v1). Every response is validated against
spec/internal/bss-internal.oas.yaml by the client (tests/apiclient.py); amounts are read
as exact Decimals.

Prices cannot start before 2026-10-02 (the day after timeline.NOW), so the scenarios run
from mid-October 2026.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.config import settings
from tests import apidata
from tests.apiclient import decimals, total
from tests.apidata import ACCOUNT, CATALOG, NIL
from tests.test_billing_db import add, at, bill, delete
from tests.timeline import iso, midnight

D = Decimal
BASE = "/internal/v1"


def usd(value: str) -> dict:
    return {"unit": "USD", "value": D(value)}


def preview(api, account: dict, offering: dict, start: str):
    return api.get(
        f"{BASE}/billingAccount/{account['id']}/firstCharge"
        f"?productOffering.id={offering['id']}&startDate={start}"
    )


@pytest.fixture
def offering(api):
    return apidata.sellable_offering(api, value=300, code="FIBER-100")


# --- context ---


def test_context(api):
    body = api.get(f"{BASE}/context").json()
    assert body == {
        "businessDate": "2026-10-01",
        "now": "2026-10-01T09:00:00Z",
        "timeZone": "Europe/Moscow",
        "utcOffset": "+03:00",
        "environment": settings.app_env,
        "operator": "i.petrova",
        "currency": "USD",
        "billingRunAt": "00:30",
        "paymentTermDays": 30,
    }


def test_context_business_date_is_the_date_in_billing_tz(api):
    api.clock.set(datetime(2026, 10, 1, 22, 30, tzinfo=UTC))  # 01:30 on Oct 2 in Moscow
    assert api.get(f"{BASE}/context").json()["businessDate"] == "2026-10-02"


@pytest.mark.parametrize(
    ("header", "operator"),
    [
        ("i.petrova", "i.petrova"),
        ("  i.petrova ", "i.petrova"),
        ("mcp-token:demo", "mcp-token:demo"),  # the MCP connector's principal
        ("a b", None),
        ("a/b", None),
        ("x" * 65, None),
    ],
)
def test_context_operator_ignores_the_proxy_header(api, header, operator):
    response = api.get(f"{BASE}/context", headers={"X-Remote-User": header})
    assert response.json()["operator"] == "i.petrova"


def test_context_takes_no_query_and_only_get(api):
    assert api.get(f"{BASE}/context?x=1").status_code == 400
    assert api.post(f"{BASE}/context", {}).status_code == 405


# --- firstCharge ---


def test_first_charge_mid_period_today(api, offering):
    account = apidata.account(api)  # cycle day 1, no credit limit
    api.clock.set(at(date(2026, 10, 15)))
    body = decimals(preview(api, account, offering, "2026-10-15"))
    assert body == {
        "billingAccount": {"id": account["id"]},
        "productOffering": {"id": offering["id"], "name": "Fiber 100"},
        "startDate": "2026-10-15",
        "period": {"start": "2026-10-15", "end": "2026-11-01"},
        "cyclePeriod": {"start": "2026-10-01", "end": "2026-11-01"},
        "billableDays": 17,
        "periodDays": 31,
        "prorated": True,
        "price": usd("300.00"),
        "amount": usd("164.52"),  # 300 x 17/31 = 164.516...
        "chargeRunDate": "2026-10-16",  # tomorrow's run sees today's order
        "billDate": "2026-11-01",
        "next": {"start": "2026-11-01", "amount": usd("300.00")},
        "balance": usd("0.00"),
        "balanceAfter": usd("-164.52"),
        "creditLimit": None,
        "exceedsCreditLimit": False,
    }


def test_first_charge_full_period_in_the_future(api, offering):
    account = apidata.account(api)
    api.clock.set(at(date(2026, 10, 15)))
    body = decimals(preview(api, account, offering, "2026-11-01"))
    assert (body["amount"], body["prorated"], body["chargeRunDate"], body["billDate"]) == (
        usd("300.00"),
        False,
        "2026-11-01",  # the run of the start date sees an order completed before it
        "2026-11-01",
    )


def test_first_charge_rounds_half_even(api):
    offering = apidata.sellable_offering(api, value=3.25)
    account = apidata.account(api)
    body = decimals(preview(api, account, offering, "2026-11-16"))
    # 3.25 x 15/30 = 1.625 exactly: half-even 1.62, half-up would give 1.63
    assert (body["amount"], body["billableDays"], body["periodDays"]) == (usd("1.62"), 15, 30)


@pytest.mark.parametrize(("year", "days", "amount"), [(2027, 28, "289.29"), (2028, 29, "279.31")])
def test_first_charge_period_length_is_the_denominator(api, offering, year, days, amount):
    account = apidata.account(api, billCycleDay=28)
    body = decimals(preview(api, account, offering, f"{year}-03-01"))
    assert (body["amount"], body["periodDays"], body["period"]["end"]) == (
        usd(amount),
        days,
        f"{year}-03-28",
    )


def test_first_charge_against_the_credit_limit(api, offering):
    account = apidata.account(api, creditLimit={"unit": "USD", "value": 100})
    api.clock.set(at(date(2026, 10, 15)))
    body = decimals(preview(api, account, offering, "2026-10-15"))
    assert (body["creditLimit"], body["exceedsCreditLimit"]) == (usd("100.00"), True)
    roomy = apidata.account(api, creditLimit={"unit": "USD", "value": 200})
    assert decimals(preview(api, roomy, offering, "2026-10-15"))["exceedsCreditLimit"] is False


def test_first_charge_without_a_following_price(api, offering):
    price_id = offering["productOfferingPrice"][0]["id"]
    end = {"startDateTime": apidata.START, "endDateTime": iso(midnight(date(2026, 11, 1)))}
    assert (
        api.patch(f"{CATALOG}/productOfferingPrice/{price_id}", {"validFor": end}).status_code
        == 200
    )
    account = apidata.account(api)
    api.clock.set(at(date(2026, 10, 15)))
    assert preview(api, account, offering, "2026-10-15").json()["next"] is None


def test_the_preview_is_what_the_billing_run_writes(api, db_conn, offering):
    """Preview, then the real order, then the run of chargeRunDate: the ABR it writes is
    the previewed one, and it lands on the previewed bill."""
    account = apidata.account(api)
    api.clock.set(at(date(2026, 10, 15)))
    body = decimals(preview(api, account, offering, "2026-10-15"))
    add(api, account, offering, date(2026, 10, 15), on=at(date(2026, 10, 15)))

    assert bill(db_conn, account, date(2026, 10, 15)).is_empty  # today's run: not yet seen
    [written] = bill(db_conn, account, date.fromisoformat(body["chargeRunDate"])).abrs
    assert (written.amount, written.period.start.isoformat(), written.period.end.isoformat()) == (
        body["amount"]["value"],
        body["period"]["start"],
        body["period"]["end"],
    )
    [issued] = bill(db_conn, account, date.fromisoformat(body["billDate"])).bills
    assert issued.amount_due == D("464.52")  # the previewed charge + November's 300.00


@pytest.mark.parametrize(
    ("query", "status"),
    [
        ("startDate=2026-10-15", 400),  # productOffering.id missing
        ("productOffering.id={o}", 400),  # startDate missing
        ("productOffering.id={o}&startDate=2026-10-15&x=1", 400),
        ("productOffering.id={o}&startDate=2026-10-15&startDate=2026-10-16", 400),
        ("productOffering.id={o}&startDate=2026-10-5", 400),
        ("productOffering.id={o}&startDate=20261015", 400),
        ("productOffering.id=nope&startDate=2026-10-15", 400),
        ("productOffering.id={o}&startDate=2026-10-14", 422),  # in the past
        (f"productOffering.id={NIL}&startDate=2026-10-15", 422),
    ],
)
def test_first_charge_errors(api, offering, query, status):
    account = apidata.account(api)
    api.clock.set(at(date(2026, 10, 15)))
    url = f"{BASE}/billingAccount/{account['id']}/firstCharge?" + query.format(o=offering["id"])
    assert api.get(url).status_code == status


def test_first_charge_unknown_account_and_missing_price(api, offering):
    api.clock.set(at(date(2026, 10, 15)))
    unknown = preview(api, {"id": NIL}, offering, "2026-10-15")
    assert unknown.status_code == 404
    euro = apidata.account(api, currency="EUR")
    response = preview(api, euro, offering, "2026-10-15")
    assert response.status_code == 422
    assert response.json()["message"] == (
        "productOffering Fiber 100 has no recurring price in EUR valid on 2026-10-15"
    )


# --- products ---


def test_open_products_with_next_charge_and_price(api, offering):
    account = apidata.account(api)
    today = date(2026, 10, 15)
    running = add(api, account, offering, today, on=at(today))
    future = add(api, account, offering, date(2026, 11, 5), on=at(today, 11))
    ending = add(api, account, offering, today, on=at(today, 12))
    delete(api, account, ending, date(2026, 10, 25), on=at(today, 13))
    ended = add(api, account, offering, today, on=at(today, 14))
    delete(api, account, ended, None, on=at(today, 15))  # terminated today: not open
    cancelled = add(api, account, offering, date(2026, 11, 5), on=at(today, 16))
    delete(api, account, cancelled, None, on=at(today, 17))  # cancelled: not open

    response = api.get(f"{BASE}/billingAccount/{account['id']}/products")
    assert total(response, api) == "3"
    rows = {p["id"]: p for p in decimals(response)}
    assert set(rows) == {running["id"], future["id"], ending["id"]}
    assert {pid: (p["status"], p["nextChargeDate"]) for pid, p in rows.items()} == {
        running["id"]: ("active", "2026-10-16"),
        future["id"]: ("created", "2026-11-05"),
        ending["id"]: ("pendingTerminate", "2026-10-16"),
    }
    assert rows[ending["id"]]["terminationDate"] == "2026-10-25"
    first = rows[running["id"]]
    assert first["productOffering"] == {
        "id": offering["id"],
        "name": "Fiber 100",
        "code": "FIBER-100",
    }
    assert first["productSpecification"]["name"] == "Fiber"
    assert first["price"] == usd("300.00")
    assert first["href"] == running["href"]


def test_products_of_an_unknown_account_and_bad_query(api):
    assert api.get(f"{BASE}/billingAccount/{NIL}/products").status_code == 404
    assert api.get(f"{BASE}/billingAccount/nope/products").status_code == 404
    account = apidata.account(api)
    # paged like every list since #312: limit is allowed, an unknown parameter is not
    assert api.get(f"{BASE}/billingAccount/{account['id']}/products?limit=1").status_code == 200
    assert api.get(f"{BASE}/billingAccount/{account['id']}/products?bogus=1").status_code == 400


# --- charges and transactions ---


@pytest.fixture
def history(api, db_conn, offering):
    """Example 4 of stage 3 with a payment in between: a 300.00 charge on Dec 1, a
    100.00 payment on Dec 5, a refund of -116.13 on Dec 20 (termination ordered Dec 19,
    effective Dec 20)."""
    account = apidata.account(api)
    product = add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    bill(db_conn, account, date(2026, 12, 1))
    api.clock.set(at(date(2026, 12, 5)))
    apidata.payment(api, account, 100)
    delete(api, account, product, date(2026, 12, 20), on=at(date(2026, 12, 19)))
    bill(db_conn, account, date(2026, 12, 20))
    return account, product


def test_charges(api, history):
    account, product = history
    response = api.get(f"{BASE}/billingAccount/{account['id']}/charges")
    assert total(response, api) == "2"
    refund, charge = decimals(response)
    assert {k: refund[k] for k in ("kind", "runDate", "period", "billableDays", "periodDays")} == {
        "kind": "refund",
        "runDate": "2026-12-20",
        "period": {"start": "2026-12-20", "end": "2027-01-01"},
        "billableDays": 12,
        "periodDays": 31,
    }
    assert (refund["amount"], refund["prorated"], refund["bill"]) == (usd("-116.13"), True, None)
    assert (charge["kind"], charge["amount"], charge["prorated"]) == (
        "charge",
        usd("300.00"),
        False,
    )
    assert charge["bill"] is not None  # on the Dec 1 bill
    assert charge["product"] == {"id": product["id"], "href": product["href"]}
    assert charge["productOffering"]["name"] == "Fiber 100"
    assert api.get(charge["href"]).status_code == 200  # the TMF678 rate


def test_transactions_carry_the_running_balance(api, history):
    account, _ = history
    response = api.get(f"{BASE}/billingAccount/{account['id']}/transactions")
    assert total(response, api) == "3"
    rows = decimals(response)
    assert [(t["type"], t["date"], t["amount"], t["balanceAfter"]) for t in rows] == [
        ("refund", "2026-12-20", usd("116.13"), usd("-83.87")),
        ("payment", "2026-12-05", usd("100.00"), usd("-200.00")),
        ("charge", "2026-12-01", usd("-300.00"), usd("-300.00")),
    ]
    assert [t["note"] for t in rows] == ["Fiber 100", "bankTransfer", "Fiber 100"]
    assert rows[1]["period"] is None
    # the newest balanceAfter is the account's balance (same sign convention)
    (balance,) = decimals(api.get(f"{ACCOUNT}/{account['id']}"))["accountBalance"]
    assert balance["amount"] == rows[0]["balanceAfter"]
    for row in rows:
        assert api.get(row["href"]).status_code == 200


def test_history_paging(api, history):
    account, _ = history
    url = f"{BASE}/billingAccount/{account['id']}/transactions"
    page = api.get(f"{url}?offset=1&limit=1")
    assert (total(page, api), page.headers["X-Result-Count"]) == ("3", "1")
    assert page.json()[0]["type"] == "payment"
    assert api.get(f"{url}?offset=3").json() == []
    for bad in ("limit=101", "limit=0", "offset=-1", "fields=id", "kind=charge"):
        assert api.get(f"{url}?{bad}").status_code == 400
        assert api.get(f"{BASE}/billingAccount/{account['id']}/charges?{bad}").status_code == 400


def test_history_of_an_unknown_account(api):
    for view in ("charges", "transactions"):
        assert api.get(f"{BASE}/billingAccount/{NIL}/{view}").status_code == 404


def test_an_unknown_internal_path_is_a_tmf_error(api):
    response = api.get(f"{BASE}/nothing")
    assert response.status_code == 404
    assert response.json()["@type"] == "Error"


@pytest.mark.parametrize("start", [date(2026, 10, 10), date(2026, 10, 15)])
def test_next_charge_is_first_uncharged_run_not_cycle_boundary(api, db_conn, offering, start):
    account = apidata.account(api)
    product = add(api, account, offering, start, on=at(start))
    api.clock.set(at(date(2026, 10, 15)))
    path = f"{BASE}/billingAccount/{account['id']}/products"
    [unpaid] = api.get(path).json()
    assert unpaid["id"] == product["id"]
    assert unpaid["nextChargeDate"] == "2026-10-16"
    [charge] = bill(db_conn, account, date(2026, 10, 16)).abrs
    assert charge.period.start == start
    api.clock.set(at(date(2026, 10, 16)))
    [paid] = api.get(path).json()
    assert paid["nextChargeDate"] == "2026-11-01"
