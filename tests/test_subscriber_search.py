"""GET /internal/v1/subscriber (#314): the Subscribers list, paged on the server with no
count, searched by prefix (name, subscriber number, short id), with per-page figures."""

from datetime import timedelta

from tests import apidata, timeline
from tests.test_api_resource_inventory import number

URL = "/internal/v1/subscriber"


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
