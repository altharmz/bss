"""One TMF slice: strict inputs, price history, navigation and bill-cycle reads.

Every API response goes through the vendored-schema validator.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from psycopg import sql

from tests import apidata
from tests.apiclient import decimals, total
from tests.apidata import ACCOUNT, CATALOG, CUSTOMER, NIL, ORDER, PARTY, PAYMENT, PRODUCT
from tests.builders.api_bill_settlement import account_with_bills, bills
from tests.timeline import START, iso, midnight, z

CYCLE = "/tmf-api/customerBillManagement/v5/billCycle"


@pytest.fixture
def shop(api):
    offering = apidata.sellable_offering(api, value=300.25)
    account = apidata.account(api, billCycleDay=10)
    api.clock.set(START + timedelta(hours=9))
    return offering, account


@pytest.fixture
def write_cases(api, db_conn):
    offering = apidata.sellable_offering(api)
    account = account_with_bills(api, db_conn, offering, date(2026, 12, 1))
    [issued] = bills(api, account)
    individual = api.get(f"{PARTY}/individual").json()[0]
    organization = apidata.organization(api)
    customer = api.get(CUSTOMER).json()[0]
    specification = api.get(f"{CATALOG}/productSpecification").json()[0]
    price = api.get(f"{CATALOG}/productOfferingPrice").json()[0]
    future = iso(midnight(date(2027, 1, 1)))
    return [
        ("POST", f"{PARTY}/individual", {"givenName": "Grace", "familyName": "Hopper"}),
        ("POST", f"{PARTY}/organization", {"name": "New organization"}),
        ("POST", CUSTOMER, apidata.customer_body(individual)),
        ("POST", ACCOUNT, apidata.account_body(customer)),
        ("POST", PAYMENT, apidata.payment_body(account, 25)),
        ("POST", ORDER, apidata.order_body(account, apidata.add_item(offering))),
        (
            "POST",
            f"{CATALOG}/productSpecification",
            {"name": "New spec", "lifecycleStatus": "Active"},
        ),
        (
            "POST",
            f"{CATALOG}/productOfferingPrice",
            {
                "name": "Monthly",
                "lifecycleStatus": "Active",
                "priceType": "recurring",
                "recurringChargePeriodType": "monthly",
                "price": {"unit": "USD", "value": 99.99},
                "validFor": {"startDateTime": future},
            },
        ),
        (
            "POST",
            f"{CATALOG}/productOffering",
            {
                "name": "New offering",
                "lifecycleStatus": "Active",
                "validFor": {"startDateTime": future},
                "productSpecification": {"id": specification["id"]},
            },
        ),
        ("PATCH", individual["href"], {"givenName": "Updated"}),
        ("PATCH", organization["href"], {"name": "Updated"}),
        ("PATCH", customer["href"], {"name": "Updated"}),
        ("PATCH", account["href"], {"name": "Updated"}),
        ("PATCH", specification["href"], {"name": "Updated"}),
        ("PATCH", price["href"], {"name": "Updated"}),
        ("PATCH", offering["href"], {"name": "Updated"}),
        ("PATCH", issued["href"], {"state": "onHold"}),
    ]


def write(api, method, path, body):
    return api.post(path, body) if method == "POST" else api.patch(path, body)


def counts(db_conn):
    tables = (
        "party",
        "customer",
        "billing_account",
        "payment",
        "product_order",
        "product",
        "product_offering",
        "product_offering_price",
        "product_specification",
        "bill_allocation",
        "customer_bill_state_change",
    )
    return {
        table: db_conn.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        ).fetchone()[0]
        for table in tables
    }


@pytest.mark.parametrize("query", ["unknown=1", "fields=missing", "fields=name&fields=state"])
def test_invalid_write_queries_are_refused_before_any_mutation(api, db_conn, write_cases, query):
    before = counts(db_conn)
    for method, path, body in write_cases:
        current = api.get(path).json() if method == "PATCH" else None
        response = write(api, method, f"{path}?{query}", body)
        assert response.status_code == 400, (method, path, response.text)
        assert counts(db_conn) == before
        if current is not None:
            assert api.get(path).json() == current


def test_every_post_refuses_foreign_or_null_type_without_writing(api, db_conn, write_cases):
    before = counts(db_conn)
    for method, path, body in write_cases:
        if method != "POST":
            continue
        for wrong in ("ForeignResource", None):
            response = api.post(path, body | {"@type": wrong})
            assert response.status_code == 400, (path, response.text)
            assert "@type" in response.json()["message"]
            assert counts(db_conn) == before


def test_fields_on_every_write_preserves_identity_location_and_full_resource(api, write_cases):
    for method, path, body in write_cases:
        response = write(api, method, f"{path}?fields=@type", body)
        assert response.status_code == (201 if method == "POST" else 200), response.text
        slim = response.json()
        assert set(slim) == {"id", "href", "@type"}
        if method == "POST":
            assert response.headers["Location"] == slim["href"]
        full = api.get(slim["href"]).json()
        assert set(full) > set(slim)


@pytest.mark.parametrize(
    "path,query",
    [
        (PRODUCT, "status=active,created"),
        (ORDER, "state=completed,rejected"),
        (ACCOUNT, "state=Active,Suspended"),
        (PAYMENT, "status=done,pending"),
        (f"{CATALOG}/productOffering", "lifecycleStatus=Active,Retired"),
        (PRODUCT, "billingAccount.id=one,two"),
        (PRODUCT, "limit=1&limit=2"),
        (PRODUCT, "offset=0&offset=1"),
    ],
)
def test_unsupported_filter_syntax_is_an_explicit_error(api, path, query):
    assert api.get(f"{path}?{query}").status_code == 400


def test_a_comma_in_a_free_text_name_is_literal(api):
    organization = apidata.organization(api, name="Smith, Jones")
    assert [r["id"] for r in api.get(f"{PARTY}/organization?name=Smith,%20Jones").json()] == [
        organization["id"]
    ]


def test_order_item_type_is_not_silently_rewritten(api, shop, db_conn):
    offering, account = shop
    response = api.post(
        ORDER, apidata.order_body(account, apidata.add_item(offering, **{"@type": "Product"}))
    )
    assert response.status_code == 400
    assert db_conn.execute("SELECT count(*) FROM product_order").fetchone() == (0,)


def test_navigation_is_complete_and_refs_are_followable(api, shop):
    offering, account = shop
    customer = api.get(CUSTOMER).json()[0]
    assert api.get(f"{customer['href']}?fields=account").json()["account"][0]["id"] == account["id"]
    for n in range(21):  # more than the default list page
        apidata.account(api, customer, name=f"Account {n}")
    apidata.account(api)  # another customer must not leak
    refs = api.get(customer["href"]).json()["account"]
    assert len(refs) == 22 and len({r["id"] for r in refs}) == 22
    assert all(r["@type"] == "AccountRef" and r["@referredType"] == "BillingAccount" for r in refs)
    assert all(api.get(r["href"]).status_code == 200 for r in refs)
    placed = apidata.order(api, account, apidata.add_item(offering))
    product = apidata.product_of(api, placed)
    assert product["orderDate"] == placed["creationDate"]
    assert product["productSpecification"]["id"] == offering["productSpecification"]["id"]
    assert api.get(product["productSpecification"]["href"]).status_code == 200
    [related] = product["relatedParty"]
    assert related["role"] == "customer" and related["partyOrPartyRole"]["id"] == customer["id"]
    assert related["partyOrPartyRole"]["@type"] == "PartyRoleRef"
    assert related["partyOrPartyRole"]["@referredType"] == "Customer"


def fee(view):
    return view["price"]["dutyFreeAmount"]["value"]


def test_order_quotes_are_exact_summed_and_immutable_after_catalog_changes(api, shop):
    offering, account = shop
    placed = decimals(
        api.post(
            ORDER,
            apidata.order_body(
                account, apidata.add_item(offering, "1"), apidata.add_item(offering, "2")
            ),
        )
    )
    assert fee(placed["orderTotalPrice"][0]) == Decimal("600.50")
    for item in placed["productOrderItem"]:
        assert fee(item["itemPrice"][0]) == Decimal("300.25")
        assert item["itemPrice"] == item["itemTotalPrice"]
        assert item["itemPrice"][0]["@type"] == "OrderPrice"
    price = offering["productOfferingPrice"][0]
    assert api.patch(price["href"], {"name": "Renamed"}).status_code == 200
    response = api.post(
        f"/internal/v1/productOffering/{offering['id']}/priceChange",
        {"amount": 350.99, "currency": "USD", "validFrom": "2026-10-15"},
    )
    assert response.status_code == 201, response.text
    api.clock.set(midnight(date(2026, 11, 10)))
    assert decimals(api.get(placed["href"])) == placed
    assert (
        decimals(api.get(f"{ORDER}?fields=orderTotalPrice"))[0]["orderTotalPrice"]
        == placed["orderTotalPrice"]
    )


def test_product_price_uses_billing_period_not_a_mid_period_catalog_change(api, shop):
    offering, account = shop  # cycle day 10; first partial period starts Oct 2
    placed = apidata.order(api, account, apidata.add_item(offering))
    product = apidata.product_of(api, placed)
    assert fee(product["productPrice"][0]) == 300.25
    assert product["productPrice"][0]["@type"] == "ProductPrice"
    changed = api.post(
        f"/internal/v1/productOffering/{offering['id']}/priceChange",
        {"amount": 350.99, "currency": "USD", "validFrom": "2026-10-15"},
    )
    assert changed.status_code == 201, changed.text
    api.clock.set(midnight(date(2026, 10, 20)))
    assert fee(decimals(api.get(product["href"]))["productPrice"][0]) == Decimal("300.25")
    api.clock.set(midnight(date(2026, 11, 10)))
    new = decimals(api.get(f"{product['href']}?fields=productPrice"))["productPrice"][0]
    assert fee(new) == Decimal("350.99")
    assert new["price"]["taxIncludedAmount"] == new["price"]["dutyFreeAmount"]
    assert fee(decimals(api.get(placed["href"]))["productOrderItem"][0]["itemPrice"][0]) == Decimal(
        "300.25"
    )


def test_future_cancelled_and_terminated_product_prices(api, shop):
    offering, account = shop
    future = apidata.order(
        api,
        account,
        apidata.add_item(offering),
        requestedStartDate=iso(midnight(date(2026, 11, 10))),
    )
    product = apidata.product_of(api, future)
    assert product["status"] == "created" and fee(product["productPrice"][0]) == 300.25
    cancelled = apidata.order(api, account, apidata.delete_item(product["id"]))
    assert cancelled["state"] == "completed"
    assert api.get(product["href"]).json()["productPrice"] == []
    placed = apidata.order(api, account, apidata.add_item(offering))
    product = apidata.product_of(api, placed)
    api.clock.set(midnight(date(2026, 10, 12)))
    apidata.order(api, account, apidata.delete_item(product["id"]))
    api.clock.set(midnight(date(2027, 1, 1)))
    assert fee(api.get(product["href"]).json()["productPrice"][0]) == 300.25


def test_rejected_orders_have_no_quoted_prices(api, shop):
    offering, account = shop
    api.patch(account["href"], {"state": "Suspended"})
    placed = apidata.order(api, account, apidata.add_item(offering))
    assert placed["state"] == "rejected" and "orderTotalPrice" not in placed
    assert "itemPrice" not in placed["productOrderItem"][0]


def test_legacy_and_delete_only_orders_do_not_invent_a_quote(api, shop, db_conn):
    from app.repository import orders

    offering, account = shop
    legacy = orders.create_order(
        db_conn,
        billing_account_id=UUID(account["id"]),
        order_date=api.clock.now(),
        requested_start_date=api.clock.today(),
        state="completed",
        items=[
            orders.NewOrderItem(
                item_id="1",
                action="add",
                state="completed",
                product_offering_id=UUID(offering["id"]),
            )
        ],
    )
    db_conn.commit()
    old = api.get(f"{ORDER}/{legacy['id']}").json()
    assert "orderTotalPrice" not in old and "itemPrice" not in old["productOrderItem"][0]
    placed = apidata.order(api, account, apidata.add_item(offering))
    product = apidata.product_of(api, placed)
    ended = apidata.order(api, account, apidata.delete_item(product["id"]))
    assert "orderTotalPrice" not in ended and "itemPrice" not in ended["productOrderItem"][0]


def test_future_product_uses_its_scheduled_start_price_and_currency(api, shop):
    offering, account = shop
    changed = api.post(
        f"/internal/v1/productOffering/{offering['id']}/priceChange",
        {"amount": 399.01, "currency": "USD", "validFrom": "2026-11-01"},
    )
    assert changed.status_code == 201
    placed = apidata.order(
        api,
        account,
        apidata.add_item(offering),
        requestedStartDate=iso(midnight(date(2026, 11, 10))),
    )
    product = apidata.product_of(api, placed)
    assert product["status"] == "created" and fee(product["productPrice"][0]) == 399.01
    assert fee(placed["productOrderItem"][0]["itemPrice"][0]) == 399.01
    assert product["productPrice"][0]["price"]["dutyFreeAmount"]["unit"] == "USD"


def test_cycle_links_dates_amounts_paging_and_read_only_contract(api, db_conn):
    offering = apidata.sellable_offering(api)
    account = account_with_bills(api, db_conn, offering, date(2026, 12, 1), date(2027, 1, 1))
    issued = bills(api, account)
    assert len(issued) == 2
    for row in issued:
        cycle = api.get(row["billCycle"]["href"]).json()
        assert cycle["@type"] == "BillCycle"
        assert cycle["billingDate"] == row["billDate"]
        assert row["billCycle"]["@type"] == "BillCycleRef"
        assert row["billCycle"]["@referredType"] == "BillCycle"
        assert row["taxExcludedAmount"] == row["taxIncludedAmount"] == row["amountDue"]
        assert row["nextBillDate"] == row["billingPeriod"]["endDateTime"]
        filtered = api.get(f"{CYCLE}?billingDate={cycle['billingDate']}&fields=billingDate")
        assert total(filtered, api) == "1"
        assert filtered.json() == [{k: cycle[k] for k in ("id", "href", "@type", "billingDate")}]
    assert api.get(f"{CYCLE}?limit=1&offset=1").headers["X-Result-Count"] == "1"
    assert api.get(f"{CYCLE}/{NIL}").status_code == 404
    assert api.get(f"{CYCLE}/invalid").status_code == 404
    assert api.get(f"{CYCLE}?billingDate=2026-12-01").status_code == 400
    for method in ("POST", "PATCH", "DELETE"):
        path = CYCLE if method == "POST" else issued[0]["billCycle"]["href"]
        assert api.request(method, path, json={}).status_code == 405


def test_bill_next_date_is_historical_not_relative_to_clock(api, db_conn):
    offering = apidata.sellable_offering(api)
    account = account_with_bills(api, db_conn, offering, date(2026, 12, 1))
    [issued] = bills(api, account)
    api.clock.set(midnight(date(2030, 1, 1)))
    assert api.get(issued["href"]).json()["nextBillDate"] == z(midnight(date(2027, 1, 1)))


def test_mcp_exposes_new_cycles_prices_and_strict_inputs(api, mcp, db_conn):
    offering = apidata.sellable_offering(api)
    account = account_with_bills(api, db_conn, offering, date(2026, 12, 1))
    [issued] = bills(api, account)
    reader, writer = mcp.token("reader", "read"), mcp.token("writer", "write")
    for name, args in (
        ("list_bill_cycles", {}),
        ("get_bill_cycle", {"cycle_id": issued["billCycle"]["id"]}),
    ):
        error, answer = mcp.result(reader, name, args)
        assert not error and answer["status"] == 200
    error, answer = mcp.result(reader, "list_products", {"filters": {"status": "active,created"}})
    assert error and answer["status"] == 400
    error, answer = mcp.result(
        writer,
        "create_individual",
        {"body": {"@type": "Organization", "givenName": "A", "familyName": "B"}},
    )
    assert error and answer["status"] == 400
    error, answer = mcp.result(
        writer, "create_organization", {"body": {"name": "Projected"}, "fields": "name"}
    )
    assert not error and set(answer["body"]) == {"id", "href", "@type", "name"}
    product = api.get(f"{PRODUCT}?billingAccount.id={account['id']}").json()[0]
    error, answer = mcp.result(
        reader,
        "get_product",
        {"product_id": product["id"], "fields": "productPrice,productSpecification,relatedParty"},
    )
    assert not error and answer["body"]["productPrice"][0]["priceType"] == "recurring"
