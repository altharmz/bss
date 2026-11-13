"""A list endpoint runs the same number of statements for a page of 1 or of 100 (#285):
the related rows of a page are read in one query each, never once per row."""

from datetime import date, timedelta

import pytest

from tests import apidata
from tests.querycount import statements
from tests.test_api_bill_settlement import account_with_bills
from tests.test_payg_rating import (  # noqa: F401 - fixtures
    build,
    next_day,
    payg,
    report,
    stubs,
)
from tests.test_payment_gateway import METHODS, save


@pytest.fixture
def three_customers(api):
    for name in ("One", "Two", "Three"):
        customer = apidata.customer(api, name=name)
        apidata.account(api, customer, name=f"{name} main")
        apidata.account(api, customer, name=f"{name} spare")


def test_customer_list(api, monkeypatch, three_customers):
    one = statements(api, monkeypatch, f"{apidata.CUSTOMER}?limit=1")
    hundred = statements(api, monkeypatch, f"{apidata.CUSTOMER}?limit=100")
    assert one == hundred


def test_billing_account_list(api, monkeypatch, three_customers):
    one = statements(api, monkeypatch, f"{apidata.ACCOUNT}?limit=1")
    hundred = statements(api, monkeypatch, f"{apidata.ACCOUNT}?limit=100")
    assert one == hundred


def test_product_list(api, monkeypatch):
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    order = apidata.order(
        api,
        account,
        *(apidata.add_item(offering, str(i)) for i in range(100)),
        requestedStartDate="2026-10-02T00:00:00+03:00",
    )
    assert order["state"] == "completed", order.get("productOrderErrorMessage", order)
    one = statements(api, monkeypatch, f"{apidata.PRODUCT}?limit=1")
    hundred = statements(api, monkeypatch, f"{apidata.PRODUCT}?limit=100")
    assert one == hundred
    assert hundred <= 18
    page = api.client.get(f"{apidata.PRODUCT}?limit=100").json()
    assert len(page) == 100
    for product in page:
        assert api.client.get(product["href"]).json() == product


def test_product_order_list(api, monkeypatch):
    offering = apidata.sellable_offering(api, tax=[apidata.tax_item("VAT", 20)])
    account = apidata.account(api)
    expected = {}
    for _ in range(100):
        order = apidata.order(
            api,
            account,
            apidata.add_item(
                offering, "parent", productOrderItem=[apidata.add_item(offering, "child")]
            ),
            requestedStartDate="2026-10-02T00:00:00+03:00",
        )
        assert order["state"] == "completed", order
        expected[order["id"]] = order
    one = statements(api, monkeypatch, f"{apidata.ORDER}?limit=1")
    hundred = statements(api, monkeypatch, f"{apidata.ORDER}?limit=100")
    assert one == hundred
    assert hundred == 10  # page + five related reads + session auth and pool checks
    page = api.client.get(f"{apidata.ORDER}?limit=100").json()
    assert len(page) == 100
    for order in page:
        assert order == expected[order["id"]]
        assert api.client.get(order["href"]).json() == order


def test_cancel_product_order_list(api, monkeypatch):
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    url = f"{apidata.ORDER.rsplit('/', 1)[0]}/cancelProductOrder"
    expected = {}
    for _ in range(100):
        order = apidata.order(
            api, account, apidata.add_item(offering), requestedInitialState="draft"
        )
        response = api.post(
            url,
            {
                "@type": "CancelProductOrder",
                "productOrder": {"id": order["id"], "@type": "ProductOrderRef"},
                "cancellationReason": "query count regression",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        expected[body["id"]] = body
    one = statements(api, monkeypatch, f"{url}?limit=1")
    hundred = statements(api, monkeypatch, f"{url}?limit=100")
    assert one == hundred
    assert hundred == 5  # page + session auth and pool checks; no related reads
    page = api.client.get(f"{url}?limit=100").json()
    assert len(page) == 100
    for cancellation in page:
        assert cancellation == expected[cancellation["id"]]
        assert api.client.get(cancellation["href"]).json() == cancellation


def test_payment_list(api, db_conn, monkeypatch):
    offering = apidata.sellable_offering(api, value=300)
    accounts = [
        account_with_bills(api, db_conn, offering, date(2026, 12, 1), date(2027, 1, 1))
        for _ in range(2)
    ]
    expected = {}
    for index in range(100):
        payment = apidata.payment(
            api,
            accounts[index % 2],
            value=350 if index < 2 else 10,
            description=f"Payment {index}",
            taxAmount={"unit": "USD", "value": 1},
        )
        expected[payment["id"]] = payment
    assert any(len(p.get("paymentItem", [])) == 2 for p in expected.values())
    assert any("paymentItem" not in p for p in expected.values())
    one = statements(api, monkeypatch, f"{apidata.PAYMENT}?limit=1")
    hundred = statements(api, monkeypatch, f"{apidata.PAYMENT}?limit=100")
    assert one == hundred == 7  # page, accounts, allocations, auth and pool checks
    page = api.client.get(f"{apidata.PAYMENT}?limit=100").json()
    assert len(page) == 100
    for payment in page:
        assert payment == expected[payment["id"]]
        assert api.client.get(payment["href"]).json() == payment


def test_payment_method_list(api, monkeypatch):
    accounts = [apidata.account(api, name=f"Cards {i}") for i in range(2)]
    expected = {}
    for index in range(100):
        method = save(api, accounts[index % 2], token=f"tok_{index}", name=f"Card {index}")
        expected[method["id"]] = method
    one = statements(api, monkeypatch, f"{METHODS}?limit=1")
    hundred = statements(api, monkeypatch, f"{METHODS}?limit=100")
    assert one == hundred == 6  # page, accounts, auth and pool checks
    page = api.client.get(f"{METHODS}?limit=100").json()
    assert len(page) == 100
    for method in page:
        assert method == expected[method["id"]]
        assert api.client.get(method["href"]).json() == method


@pytest.mark.usefixtures("stubs")
@pytest.mark.parametrize("rated", [False, True])
def test_product_usage_list(api, db_conn, monkeypatch, payg, rated):  # noqa: F811 - fixture
    world = payg("10", kinds=("data",))
    start = api.clock.now()
    for index in range(100):
        report(
            api,
            world.product,
            "data" if index % 2 else "sms",
            1,
            start + timedelta(seconds=index),
            record=f"batch-{index}",
        )
    if rated:
        next_day(api, db_conn)
    url = "/tmf-api/productUsageManagement/v5/productUsage"
    url += f"?product.id={world.product['id']}"
    # Advancing to the rating day expires the session; the checked client renews it
    # before the raw client measures SQL (without counting a login).
    assert api.get(f"{url}&limit=1").status_code == 200
    one = statements(api, monkeypatch, f"{url}&limit=1")
    hundred = statements(api, monkeypatch, f"{url}&limit=100")
    assert one == hundred == (7 if rated else 6)
    page = api.client.get(f"{url}&limit=100").json()
    assert len(page) == 100
    if rated:
        assert any(p["usageProductPrice"] == [] for p in page)
        assert any(p["usageProductPrice"] for p in page)
    else:
        assert all("usageProductPrice" not in p for p in page)
    for usage in page:
        assert api.client.get(usage["href"]).json() == usage
