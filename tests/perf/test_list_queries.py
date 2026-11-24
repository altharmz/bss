"""A list endpoint runs the same number of statements for a page of 1 or of 100 (#285):
the related rows of a page are read in one query each, never once per row."""

from datetime import date, datetime, timedelta

import pytest

from app.config import settings
from app.repository import billing
from tests import apidata
from tests.builders.billing.api_bill_settlement import account_with_bills
from tests.builders.billing.billing_db import add, at, bill
from tests.builders.prepaid.api_adjust import post as adjust
from tests.builders.prepaid.api_reserve import post as reserve
from tests.builders.prepaid.api_topup import BASE as BALANCE
from tests.builders.prepaid.api_topup import post as topup
from tests.builders.prepaid.api_transfer import pair
from tests.builders.prepaid.api_transfer import post as transfer
from tests.builders.prepaid.payg_rating import next_day, payg, report  # noqa: F401 - fixtures
from tests.builders.prepaid.payment_gateway import METHODS, save
from tests.builders.prepaid.prepaid_plans import build, stubs  # noqa: F401 - fixtures
from tests.builders.tmf.api_financial_account import financial, link
from tests.builders.tmf.api_party_account import create as create_party_account
from tests.perf.guard import counting, statements


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


@pytest.fixture
def hundred_bills(api, db_conn):
    offering = apidata.sellable_offering(
        api,
        value=300,
        tax=[apidata.tax_item("Z-VAT", 20), apidata.tax_item("A-local", 3)],
    )
    accounts = []
    for index in range(2):
        party = apidata.individual(api, given=f"Bill {index}")
        customer = apidata.customer(api, party, name=f"Customer {index}")
        fa = financial(api, party, name=f"Receivables {index}")
        account = apidata.account(api, customer, financialAccount=link(fa))
        for _ in range(2):
            add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
        accounts.append(account)
    for month in range(50):
        day = date(2026 + (11 + month) // 12, (11 + month) % 12 + 1, 1)
        for account in accounts:
            bill(db_conn, account, day)
    for account in accounts:
        apidata.payment(api, account, value=100)
        apidata.payment(api, account, value=50)
    return accounts


def test_customer_bill_list(api, monkeypatch, hundred_bills):
    one = statements(api, monkeypatch, f"{apidata.BILL}?limit=1")
    hundred = statements(api, monkeypatch, f"{apidata.BILL}?limit=100")
    assert one == hundred == 11  # page, six related reads, session auth and pool checks
    page = api.client.get(f"{apidata.BILL}?limit=100").json()
    assert len(page) == 100
    assert sum(bool(b["appliedPayment"]) for b in page) == 2
    for body in page:
        assert body["taxExcludedAmount"]["value"] == 600
        assert body["amountDue"]["value"] == 738
        assert [t["taxCategory"] for t in body["taxItem"]] == ["A-local", "Z-VAT"]
        assert [t["taxAmount"]["value"] for t in body["taxItem"]] == [18, 120]
        assert "financialAccount" in body
        if body["appliedPayment"]:
            assert body["remainingAmount"]["value"] == 588
            ids = [p["payment"]["id"] for p in body["appliedPayment"]]
            assert len(ids) == 2 and ids == sorted(ids)
        assert api.client.get(body["href"]).json() == body
    assert api.client.get(f"{apidata.BILL}?offset=100").json() == []


def test_applied_billing_rate_list(api, monkeypatch, hundred_bills):
    one = statements(api, monkeypatch, f"{apidata.RATE}?limit=1")
    hundred = statements(api, monkeypatch, f"{apidata.RATE}?limit=100")
    assert one == hundred == 7  # page, accounts, offerings, session auth and pool checks
    page = api.client.get(f"{apidata.RATE}?limit=100").json()
    assert len(page) == 100
    for body in page:
        assert body["taxExcludedAmount"]["value"] == 300
        assert body["taxIncludedAmount"]["value"] == 369
        assert body["isBilled"] is True
        assert api.client.get(body["href"]).json() == body
    assert api.client.get(f"{apidata.RATE}?offset=200").json() == []


def test_bill_cycle_list(api, db_conn, monkeypatch, hundred_bills):
    # Fifty real runs have stored 30-day terms; fifty empty runs fall back to today's
    # configured term, which must never replace a term already stored on an issued bill.
    for index in range(50):
        billing.insert_run(
            db_conn,
            date(2040, 1, 1) + timedelta(days=index),
            now=api.clock.now(),
            trigger="scheduled",
        )
    db_conn.commit()
    monkeypatch.setattr(settings, "bill_payment_term_days", 17)
    url = "/tmf-api/customerBillManagement/v5/billCycle"
    assert api.get(f"{url}?limit=1").status_code == 200  # warm the typed kind filter
    one = statements(api, monkeypatch, f"{url}?limit=1")
    hundred = statements(api, monkeypatch, f"{url}?limit=100")
    assert one == hundred == 6  # page, due dates, session auth and pool checks
    page = api.client.get(f"{url}?limit=100").json()
    assert len(page) == 100
    terms = []
    for body in page:
        terms.append(
            (
                datetime.fromisoformat(body["paymentDueDate"])
                - datetime.fromisoformat(body["billingDate"])
            ).days
        )
        assert api.client.get(body["href"]).json() == body
    assert sorted(terms) == [17] * 50 + [30] * 50
    assert api.client.get(f"{url}?offset=100").json() == []


def _same_bodies(api, url: str, expected: dict | None = None) -> list[dict]:
    """The full page, each body equal to its single-item read (and to `expected`, by id)."""
    page = api.client.get(f"{url}{'&' if '?' in url else '?'}limit=100").json()
    for body in page:
        if expected is not None:
            assert body == expected[body["id"]]
        assert api.client.get(body["href"]).json() == body
    return page


def test_communication_message_list(api, monkeypatch):
    url = "/tmf-api/communicationManagement/v4/communicationMessage"
    expected = {}
    for index in range(12):
        body = {
            "@type": "CommunicationMessage",
            "messageType": "SMS",
            "content": f"Message {index}",
            "receiver": [{"@type": "Receiver", "phoneNumber": f"1212555{index:04d}"}],
            "sender": {"@type": "Sender", "name": "ops"},
        }
        response = api.post(url, body)
        assert response.status_code == 201, response.text
        expected[response.json()["id"]] = response.json()
    one = statements(api, monkeypatch, f"{url}?limit=1")
    hundred = statements(api, monkeypatch, f"{url}?limit=100")
    assert one == hundred == 5  # page, session auth and pool checks; no related reads
    assert len(_same_bodies(api, url, expected)) == 12


def test_product_offering_list(api, monkeypatch):
    url = f"{apidata.CATALOG}/productOffering"
    specs = [
        apidata._post(
            api,
            f"{apidata.CATALOG}/productSpecification",
            {"@type": "ProductSpecification", "name": f"Spec {i}", "lifecycleStatus": "Active"},
        )
        for i in range(2)
    ]
    offerings = []
    for index in range(12):
        prices = [
            apidata._post(
                api,
                f"{apidata.CATALOG}/productOfferingPrice",
                {
                    "@type": "ProductOfferingPrice",
                    "name": f"Price {index}.{n}",
                    "lifecycleStatus": "Active",
                    "priceType": "oneTime",
                    "price": {"unit": ("USD", "EUR")[n], "value": n + 1},
                },
            )
            for n in range(2 - index % 3)
        ]
        body = {
            "@type": "ProductOffering",
            "name": f"Offering {index}",
            "lifecycleStatus": "Active",
            "productOfferingPrice": [
                {"id": p["id"], "@type": "ProductOfferingPriceRef"} for p in prices
            ],
        }
        if index % 4 != 3:
            spec = specs[index % 2]
            body["productSpecification"] = {"id": spec["id"], "@type": "ProductSpecificationRef"}
        offerings.append(apidata._post(api, url, body))
    for name, members in (("Home", offerings[::2]), ("Business", offerings[::3][:3])):
        apidata._post(
            api,
            f"{apidata.CATALOG}/category",
            {"name": name, "productOffering": [{"id": o["id"]} for o in members]},
        )
    one = statements(api, monkeypatch, f"{url}?limit=1")
    hundred = statements(api, monkeypatch, f"{url}?limit=100")
    assert one == hundred == 8  # page, specs, prices, categories, auth and pool checks
    page = _same_bodies(api, url)
    assert len(page) == 12
    assert {len(o["productOfferingPrice"]) for o in page} == {0, 1, 2}
    assert {len(o["category"]) for o in page} == {0, 1, 2}
    assert sum("productSpecification" in o for o in page) == 9
    for body in page:
        assert [p["name"] for p in body["productOfferingPrice"]] == sorted(
            p["name"] for p in body["productOfferingPrice"]
        )


def test_financial_account_list(api, monkeypatch):
    url = "/tmf-api/accountManagement/v5/financialAccount"
    for index in range(6):
        party = apidata.individual(api, given=f"Owner {index}")
        fa = financial(api, party, name=f"Receivables {index}")
        if index == 5:
            continue  # nothing linked: no balance, no relationship
        customer = apidata.customer(api, party, name=f"Customer {index}")
        usd = apidata.account(api, customer, name=f"USD {index}", financialAccount=link(fa))
        apidata.payment(api, usd, 10 * (index + 1))
        if index % 2 == 0:
            eur = apidata.account(
                api, customer, name=f"EUR {index}", currency="EUR", financialAccount=link(fa)
            )
            apidata.payment(api, eur, index + 1, unit="EUR")
            create_party_account(api, "partyAccount", party, financialAccount=link(fa))
    with counting(monkeypatch) as one:
        assert api.client.get(f"{url}?limit=1").status_code == 200
    with counting(monkeypatch) as hundred:
        assert api.client.get(f"{url}?limit=100").status_code == 200
    # page, owners, linked, billing accounts, balances, auth and pool checks; the ledger
    # schema check is asked once per pooled connection (#305), so only by a fresh one
    schema_check = "to_regclass('funding_consumption')"
    assert len([q for q in one if schema_check not in q]) == 9
    assert len([q for q in hundred if schema_check not in q]) == 9
    page = _same_bodies(api, url)
    assert len(page) == 6
    assert {len(b["accountBalance"]) for b in page} == {0, 1, 2}
    assert {len(b["accountRelationship"]) for b in page} == {0, 1, 3}


@pytest.mark.parametrize("resource", ["partyAccount", "settlementAccount"])
def test_party_account_list(api, monkeypatch, resource):
    url = f"/tmf-api/accountManagement/v5/{resource}"
    expected = {}
    for index in range(6):
        party = apidata.individual(api, given=f"Partner {index}")
        extra = {}
        if index % 2 == 0:
            extra["financialAccount"] = link(financial(api, party, name=f"Payouts {index}"))
        body = create_party_account(api, resource, party, name=f"Account {index}", **extra)
        expected[body["id"]] = body
    assert api.get(f"{url}?limit=1").status_code == 200  # warm the typed kind filter
    one = statements(api, monkeypatch, f"{url}?limit=1")
    hundred = statements(api, monkeypatch, f"{url}?limit=100")
    assert one == hundred == 7  # page, owners, financial accounts, auth and pool checks
    page = _same_bodies(api, url, expected)
    assert sum("financialAccount" in b for b in page) == 3


def test_bill_on_demand_list(api, monkeypatch):
    url = "/tmf-api/customerBillManagement/v5/customerBillOnDemand"
    expected = {}
    for index in range(6):
        account = apidata.account(api, name=f"On demand {index}")
        response = api.post(url, {"billingAccount": {"id": account["id"]}})
        assert response.status_code == 201, response.text
        expected[response.json()["id"]] = response.json()
    one = statements(api, monkeypatch, f"{url}?limit=1")
    hundred = statements(api, monkeypatch, f"{url}?limit=100")
    assert one == hundred == 6  # page, accounts, session auth and pool checks
    assert len(_same_bodies(api, url, expected)) == 6


@pytest.fixture
def balance_actions(api, db_conn):
    """Top-ups, adjustments, holds and transfers in both directions on two prepaid
    accounts, keyed by the API resource (the creation responses)."""
    tx, rx = pair(api, db_conn)
    created: dict[str, list[dict]] = {name: [] for name in BALANCE_LISTS}
    for acc, other in ((tx, rx), (rx, tx)):
        for _ in range(3):
            created["topupBalance"].append(topup(api, acc, 20).json())
        for value in (2, -1, 999):  # a credit, a debit and a refused debit
            created["adjustBalance"].append(adjust(api, acc, value).json())
        for value in (1, 2, 999):  # the last is refused
            created["reserveBalance"].append(reserve(api, acc, value).json())
        for _ in range(3):
            created["transferBalance"].append(transfer(api, acc, other, 1).json())
    return created


BALANCE_LISTS = ("topupBalance", "adjustBalance", "reserveBalance", "transferBalance")


@pytest.mark.parametrize("name", [*BALANCE_LISTS, "balanceActionHistory"])
def test_balance_action_lists(api, monkeypatch, balance_actions, name):
    url = f"{BALANCE}/{name}"
    one = statements(api, monkeypatch, f"{url}?limit=1")
    hundred = statements(api, monkeypatch, f"{url}?limit=100")
    assert one == hundred == 6  # page, accounts, session auth and pool checks
    page = api.client.get(f"{url}?limit=100").json()
    created = [
        body for kind in BALANCE_LISTS if name in (kind, "balanceActionHistory")
        for body in balance_actions[kind]
    ]  # fmt: skip
    assert sorted(b["id"] for b in page) == sorted(b["id"] for b in created)
    assert {b["partyAccount"]["id"] for b in page} == {
        b["partyAccount"]["id"] for b in created
    } and len({b["partyAccount"]["id"] for b in page}) == 2
    for body in page:
        assert api.client.get(body["href"]).json() == body
    if name != "balanceActionHistory":
        by_id = {b["id"]: b for b in page}
        for body in created:
            assert by_id[body["id"]] == body
