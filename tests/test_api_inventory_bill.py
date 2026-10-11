"""TMF637 product and TMF678 customerBill / appliedCustomerBillingRate: read-only views.

Orders (stage 2, Checkpoint 4) and the billing run (stage 3/4) are what write these
tables, so the rows here are inserted through the repository and read through the API.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.repository import billing, orders, products
from tests import factories
from tests.apiclient import decimals
from tests.apidata import BILL, PRODUCT, RATE
from tests.factories import NOW, days


@pytest.fixture
def world(api, db_conn):
    w = factories.world(db_conn)  # a product on 2026-01-01, one billing run
    db_conn.commit()
    return w


def terminate(db_conn, w, on: date) -> None:
    item = orders.create_order(
        db_conn,
        billing_account_id=w.account["id"],
        order_date=NOW,
        items=[orders.NewOrderItem(item_id="7", action="delete", product_id=w.product["id"])],
    )["items"][0]
    products.terminate_product(
        db_conn, w.product["id"], termination_date=on, terminated_by_item_id=item["id"]
    )
    db_conn.commit()


# --- TMF637 ---


def test_product_view(api, world):
    listed = api.get(PRODUCT)
    assert listed.headers["X-Total-Count"] == "1"
    product = listed.json()[0]
    pid, acc, off = world.product["id"], world.account, world.offering
    assert product["href"] == f"{PRODUCT}/{pid}"
    assert product["name"] == "Fiber 100"
    assert product["status"] == "active"
    assert product["startDate"] == "2025-12-31T21:00:00Z"  # 2026-01-01 in Moscow
    assert "terminationDate" not in product
    assert product["billingAccount"]["id"] == str(acc["id"])
    assert product["billingAccount"]["@referredType"] == "BillingAccount"
    assert product["productOffering"]["id"] == str(off["id"])
    (related,) = product["productOrderItem"]
    assert related["orderItemAction"] == "add" and related["role"] == "creation"
    assert related["orderItemId"] == "1"
    assert related["orderHref"].endswith(f"/productOrder/{related['orderId']}")
    assert api.get(product["href"]).json() == product


def test_terminated_product_points_at_both_order_items(api, world, db_conn):
    terminate(db_conn, world, date(2026, 1, 20))
    product = api.get(f"{PRODUCT}/{world.product['id']}").json()
    assert product["status"] == "terminated"
    assert product["terminationDate"] == "2026-01-19T21:00:00Z"  # 2026-01-20, exclusive
    actions = [(i["orderItemAction"], i["role"]) for i in product["productOrderItem"]]
    assert actions == [("add", "creation"), ("delete", "termination")]


def test_product_filters(api, world, db_conn):
    other = factories.account(db_conn, name="Petrov")
    factories.product(db_conn, other["id"], world.offering["id"], date(2026, 2, 1))
    db_conn.commit()
    by_account = api.get(f"{PRODUCT}?billingAccount.id={world.account['id']}").json()
    assert [p["id"] for p in by_account] == [str(world.product["id"])]
    assert len(api.get(f"{PRODUCT}?status=active").json()) == 2
    assert api.get(f"{PRODUCT}?status=terminated").json() == []
    assert api.get(f"{PRODUCT}?productOffering.id={world.offering['id']}").status_code == 400


@pytest.mark.parametrize(("method", "path"), [("POST", ""), ("PATCH", "/x"), ("DELETE", "/x")])
def test_products_are_not_written_through_tmf637(api, world, method, path):
    url = f"{PRODUCT}/{world.product['id']}" if path else PRODUCT
    response = api.request(method, url, json={"status": "active"})
    assert response.status_code == 405
    assert response.json()["@type"] == "Error"


# --- TMF678 ---


def test_bill_and_rate_lists_are_empty_before_any_billing_run(api):
    for url in (BILL, RATE):
        response = api.get(url)
        assert response.status_code == 200 and response.json() == []
        assert response.headers["X-Total-Count"] == "0"


def test_applied_billing_rates(api, world, db_conn):
    feb = factories.charge(db_conn, world, days(date(2026, 2, 1), date(2026, 3, 1)))
    refund = billing.insert_abr(
        db_conn,
        product_id=world.product["id"],
        billing_account_id=world.account["id"],
        charge_type="refund",
        amount=Decimal("-50.00"),
        currency="RUB",
        period=days(date(2026, 2, 15), date(2026, 3, 1)),
        billing_run_id=world.run["id"],
        applied_at=world.run["started_at"],
        refunds_rate_id=feb["id"],
    )
    db_conn.commit()
    response = api.get(RATE)
    # both rows were inserted in one transaction: same created_at, so list order is by id
    by_id = {rate["id"]: rate for rate in decimals(response)}
    charge, credit = by_id[str(feb["id"])], by_id[str(refund["id"])]
    assert charge["appliedBillingRateType"] == "appliedBillingCharge"
    assert credit["appliedBillingRateType"] == "appliedBillingCredit"
    assert charge["taxExcludedAmount"] == {"unit": "RUB", "value": Decimal("100.00")}
    assert credit["taxExcludedAmount"] == {"unit": "RUB", "value": Decimal("-50.00")}
    assert charge["periodCoverage"] == {
        "startDateTime": "2026-01-31T21:00:00Z",
        "endDateTime": "2026-02-28T21:00:00Z",
    }
    assert charge["isBilled"] is False and "bill" not in charge
    assert charge["product"]["id"] == str(world.product["id"])
    assert charge["billingAccount"]["id"] == str(world.account["id"])
    assert api.get(charge["href"]).json()["id"] == charge["id"]


def test_customer_bill_and_is_billed(api, world, db_conn):
    bill = billing.insert_bill(
        db_conn,
        billing_account_id=world.account["id"],
        currency="RUB",
        bill_period=days(date(2026, 1, 1), date(2026, 2, 1)),
        bill_date=date(2026, 2, 1),
        amount_due=Decimal("499.90"),
        billing_run_id=world.run["id"],
        payment_due_date=date(2026, 3, 3),
    )
    jan = factories.charge(db_conn, world, days(date(2026, 1, 1), date(2026, 2, 1)))
    db_conn.execute(
        "UPDATE applied_customer_billing_rate SET customer_bill_id = %s WHERE id = %s",
        (bill["id"], jan["id"]),
    )
    factories.charge(db_conn, world, days(date(2026, 2, 1), date(2026, 3, 1)))
    db_conn.commit()

    body = decimals(api.get(f"{BILL}/{bill['id']}"))
    assert body["amountDue"] == {"unit": "RUB", "value": Decimal("499.90")}
    assert body["state"] == "new"
    assert body["billDate"] == "2026-01-31T21:00:00Z"
    assert body["billingPeriod"] == {
        "startDateTime": "2025-12-31T21:00:00Z",
        "endDateTime": "2026-01-31T21:00:00Z",
    }
    assert body["billingAccount"]["id"] == str(world.account["id"])

    billed = api.get(f"{RATE}?isBilled=true").json()
    assert [r["id"] for r in billed] == [str(jan["id"])]
    assert billed[0]["bill"]["id"] == str(bill["id"])
    assert billed[0]["bill"]["@type"] == "CustomerBillRef"
    assert len(api.get(f"{RATE}?isBilled=false").json()) == 1
    assert api.get(f"{RATE}?isBilled=maybe").status_code == 400
    assert len(api.get(f"{RATE}?product.id={world.product['id']}").json()) == 2
    assert len(api.get(f"{BILL}?billingAccount.id={world.account['id']}&state=new").json()) == 1
    assert api.get(f"{BILL}?state=sent").json() == []


def test_balance_includes_charges(api, world, db_conn):
    factories.charge(db_conn, world, days(date(2026, 1, 1), date(2026, 2, 1)), "100.00")
    db_conn.commit()
    href = f"/tmf-api/accountManagement/v5/billingAccount/{world.account['id']}"
    (balance,) = decimals(api.get(href))["accountBalance"]
    assert balance["amount"] == {"unit": "RUB", "value": Decimal("-100.00")}  # customer owes


@pytest.mark.parametrize(
    ("method", "url"),
    [("POST", BILL), ("POST", RATE), ("PATCH", RATE), ("DELETE", BILL)],
)
def test_bills_and_rates_are_created_only_by_the_run(api, world, method, url):
    """Bills and ABRs are created by the billing run only; a bill's state alone may be
    patched (tests/test_api_bill_settlement.py), an ABR not at all."""
    target = url if method == "POST" else f"{url}/{world.product['id']}"
    assert api.request(method, target, json={}).status_code == 405
