"""GET /internal/v1/subscriber (#314): the Subscribers list, paged on the server with no
count, searched by prefix (name, subscriber number, short id), with per-page figures."""

from datetime import date, timedelta

import pytest

from app.repository import subscribers
from tests import apidata, factories, timeline
from tests.builders.api_resource_inventory import number
from tests.builders.number_persistence import assignment
from tests.builders.number_persistence import number as number_row
from tests.builders.prepaid_orders import shop
from tests.builders.settlement_db import make_payment
from tests.builders.tax_engine import CA, set_address

URL = "/internal/v1/subscriber"


pytestmark = pytest.mark.usefixtures("match_path")


def names(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [row["name"] for row in response.json()]


def subscriber(api, customer: str, account: str, **over):
    cust = apidata.customer(api, name=customer)
    return apidata.account(api, cust, name=account, **over)


def numbered_line(api, acc, value: str):
    """An active postpaid line holding `value`."""
    offering = apidata.sellable_offering(api, name=f"Line {value}")
    spec = api.patch(
        offering["productSpecification"]["href"],
        {"resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]},
    )
    assert spec.status_code == 200, spec.text
    resource = number(
        api,
        value=value,
        resourceSpecification={"id": "msisdn", "@type": "ResourceSpecificationRef"},
        administrativeState="unlocked",
    )
    item = apidata.add_item(
        offering,
        product={
            "@type": "ProductRefOrValue",
            "realizingResource": [
                {"id": resource["id"], "@type": "ResourceRef", "@referredType": "LogicalResource"}
            ],
        },
    )
    api.clock.set(timeline.START + timedelta(hours=1))  # the offering's price starts then
    placed = apidata.order(api, acc, item)
    assert placed["state"] == "completed", placed.get("productOrderErrorMessage")


def test_name_prefix_case_insensitive_customer_or_account(api):
    subscriber(api, "Ivan Petrov", "Main")
    subscriber(api, "Olga Smirnova", "Ivanovo branch")
    subscriber(api, "Anna Ivanova", "Home")
    assert names(api.get(f"{URL}?q=ivan")) == ["Main", "Ivanovo branch"]
    assert names(api.get(f"{URL}?q=IVAN%20P")) == ["Main"]
    assert names(api.get(f"{URL}?q=ova")) == []  # a prefix, not a part of the name
    assert names(api.get(f"{URL}?q=%25")) == []  # LIKE wildcards are matched literally


def test_short_id_and_full_id(api):
    acc = subscriber(api, "Ivan Petrov", "Main")
    other = subscriber(api, "Olga Smirnova", "Main")
    short = acc["id"][:8].upper()
    assert acc["id"] in [r["id"] for r in api.get(f"{URL}?q=BA-{short}").json()]
    assert acc["id"] in [r["id"] for r in api.get(f"{URL}?q={short[:5].lower()}").json()]
    assert [r["id"] for r in api.get(f"{URL}?q={acc['id']}").json()] == [acc["id"]]
    assert other["id"] not in [r["id"] for r in api.get(f"{URL}?q={acc['id']}").json()]


def test_subscriber_number_prefix(api):
    acc = subscriber(api, "Ivan Petrov", "Main")
    subscriber(api, "Olga Smirnova", "Main")
    numbered_line(api, acc, "12025550177")
    assert [r["id"] for r in api.get(f"{URL}?q=1202555").json()] == [acc["id"]]
    assert [r["id"] for r in api.get(f"{URL}?q=%2B12025550177").json()] == [acc["id"]]
    assert api.get(f"{URL}?q=1999").json() == []


def test_filters_paging_and_page_figures(api):
    ivan = apidata.customer(api, name="Ivan Petrov")
    first = apidata.account(api, ivan, name="Main")
    apidata.account(api, ivan, name="Spare", ratingType="prepaid")
    subscriber(api, "Olga Smirnova", "Main", state="Suspended")
    numbered_line(api, first, "12025550178")
    page = api.get(f"{URL}?limit=1")
    assert len(page.json()) == 1 and 'rel="next"' in page.headers["Link"]
    assert "X-Total-Count" not in page.headers
    row = page.json()[0]
    assert row["id"] == first["id"] and row["activeProducts"] == 1
    assert row["customerAccounts"] == 2 and row["customer"]["name"] == "Ivan Petrov"
    assert row["balance"]["unit"] == "USD"
    assert names(api.get(f"{URL}?ratingType=prepaid")) == ["Spare"]
    assert names(api.get(f"{URL}?state=Suspended")) == ["Main"]
    assert api.get(f"{URL}?state=Bogus").status_code == 400
    assert api.get(f"{URL}?q=a&q=b").status_code == 400
    last = api.get(f"{URL}?offset=2&limit=1")
    assert len(last.json()) == 1 and "Link" not in last.headers


def test_the_prefix_searches_use_indexes(db_conn):
    db_conn.execute("SET enable_seqscan = off")
    for query in (
        "SELECT id FROM customer WHERE lower(name) LIKE 'ivan%'",
        "SELECT id FROM billing_account WHERE lower(name) LIKE 'ivan%'",
        "SELECT id FROM subscriber_number WHERE resource_specification_id = 'msisdn'"
        " AND value LIKE '1202%'",
    ):
        plan = "\n".join(r[0] for r in db_conn.execute("EXPLAIN " + query))
        assert "Seq Scan" not in plan, plan


def test_the_number_and_bucket_pages_read_their_order_index(db_conn):
    """Numbers, SIMs and Balances page `ORDER BY created_at, id` (#314, migration 0073)."""
    db_conn.execute("SET enable_seqscan = off")
    db_conn.execute("SET enable_sort = off")
    for table in ("subscriber_number", "prepaid_bucket"):
        query = f"SELECT id FROM {table} ORDER BY created_at, id LIMIT 51"  # noqa: S608
        plan = "\n".join(r[0] for r in db_conn.execute("EXPLAIN " + query))
        assert f"{table}_created_at_id_idx" in plan, plan


def test_service_address_missing_lists_prepaid_accounts_with_live_lines_and_no_address(
    api, db_conn
):
    """#207: the accounts whose new lines are refused and whose charges get the flat tax."""
    off, _ = shop(api, db_conn, funds="0", numbers=False)

    def prepaid_line(name: str):
        acc = apidata.account(api, name=name)
        db_conn.execute(
            "UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (acc["id"],)
        )
        make_payment(db_conn, dict(acc, currency="USD"), "100")
        db_conn.commit()
        order = apidata.order(api, acc, apidata.add_item(off))
        assert order["state"] == "inProgress", order.get("productOrderErrorMessage")
        return acc

    first = prepaid_line("No address 1")
    with_address = prepaid_line("Has address")
    assert set_address(api, with_address, CA).status_code == 200
    ended = prepaid_line("Ended line")
    db_conn.execute(
        "UPDATE product SET status = 'terminated' WHERE billing_account_id = %s", (ended["id"],)
    )
    db_conn.commit()
    second = prepaid_line("No address 2")
    empty = apidata.account(api, name="No lines")
    db_conn.execute(
        "UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (empty["id"],)
    )
    db_conn.commit()
    subscriber(api, "Postpaid Customer", "Postpaid")

    url = f"{URL}?serviceAddress=missing"
    assert [r["id"] for r in api.get(url).json()] == [first["id"], second["id"]]
    page = api.get(f"{url}&limit=1")
    assert [r["id"] for r in page.json()] == [first["id"]] and 'rel="next"' in page.headers["Link"]
    last = api.get(f"{url}&limit=1&offset=1")
    assert [r["id"] for r in last.json()] == [second["id"]] and "Link" not in last.headers
    assert names(api.get(f"{url}&q=no%20address%202")) == ["No address 2"]
    assert api.get(f"{URL}?serviceAddress=present").status_code == 400
    assert api.get(f"{url}&serviceAddress=missing").status_code == 400


def numbered_accounts(conn):
    """Six accounts with a live line numbered 1202555010<i>, one each with a cancelled and
    an ended claim on a number of the same prefix, and one with no number (#365)."""
    off = factories.offering(conn)
    live = []
    for i in range(9):
        acc = factories.account(conn, name=f"Holder {i}")
        if i == 8:
            break
        prod = factories.product(conn, acc["id"], off["id"], date(2026, 1, 1))
        claim = {"cancelled": i == 6, "end": date(2026, 1, 10) if i == 7 else None}
        assignment(conn, number_row(conn, f"1202555010{i}"), prod, **claim)
        if i < 6:
            live.append(acc["id"])
    return live


def brute_force(conn, ids):
    """`ids` in the list order, read without the search."""
    rows = conn.execute(
        "SELECT id FROM billing_account WHERE id = ANY(%s) ORDER BY created_at, id", (ids,)
    )
    return [r[0] for r in rows]


@pytest.mark.parametrize("cap", [0, 2, 1000])
def test_number_search_pages_match_a_brute_force_query(db_conn, monkeypatch, cap):
    """#365: a broad number prefix walks the order index with a correlated test per
    account, a narrow one is cut from the probe; both give the same pages in the same
    order, and a cancelled or ended claim is found by neither. Cap 0 walks every search,
    1000 probes every one, 2 walks the broad prefix and probes the full number."""
    monkeypatch.setattr(subscribers, "MATCH_CAP", cap)
    live = numbered_accounts(db_conn)
    expected = brute_force(db_conn, live)

    def pages(q, limit):
        found, offset, more = [], 0, True
        while more:
            rows, more = subscribers.search(
                db_conn, q=q, state=None, rating_type=None, offset=offset, limit=limit
            )
            assert len(rows) == limit or not more
            found += [r["id"] for r in rows]
            offset += limit
        return found

    for q in ("12025", "1202555", "+1202555010"):
        assert pages(q, 2) == expected
        assert pages(q, 50) == expected
    assert pages("12025550103", 2) == [live[3]]
    assert pages("12025550106", 2) == []  # cancelled
    assert pages("12025550107", 2) == []  # ended
    assert pages("1999", 2) == []


def test_a_walking_page_uses_the_page_form_of_a_match(db_conn, monkeypatch):
    """#365: over the cap the page tests each row with a match's page form (the correlated
    number test), not its probe form (the set of every matching number)."""
    monkeypatch.setattr(subscribers, "MATCH_CAP", 0)
    factories.account(db_conn, name="Walker")
    params = {"name": "walker%"}
    probe = "lower(a.name) LIKE %(name)s"
    page = "lower(a.name) LIKE %(name)s AND true"
    for matches, want in (
        ([("billing_account a", probe, page)], f"({page})"),
        ([("billing_account a", probe)], f"({probe})"),
    ):
        match, offset = subscribers.matching(
            db_conn,
            alias="a",
            conds=["true"],
            matches=matches,
            params=params,
            offset=0,
            limit=10,
        )
        assert (match, offset) == (want, 0)
