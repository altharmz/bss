"""The history of one product (#458): status changes, offering changes and number
assignments as one list, newest first, paged; an unknown product is a 404."""

import re
from datetime import timedelta

import pytest

from app.repository import product_history
from tests import apidata, timeline
from tests.apidata import CATALOG, NIL, ORDER
from tests.builders.orders.api_order_modify import modify_item
from tests.builders.orders.api_suspension import status_item
from tests.builders.resources.api_resource_inventory import number
from tests.builders.resources.number_orders import refs
from tests.perf.guard import NO_SEQSCAN, plan

HISTORY = "/internal/v1/product/{}/history"
DAY0 = timeline.START.date()


def at(api, days: int):
    api.clock.set(timeline.START + timedelta(days=days, hours=9))
    return DAY0 + timedelta(days=days)


def same_spec_offering(api, offering: dict, name: str) -> dict:
    price = api.post(
        f"{CATALOG}/productOfferingPrice",
        {
            "@type": "ProductOfferingPrice",
            "name": "Monthly",
            "lifecycleStatus": "Active",
            "priceType": "recurring",
            "recurringChargePeriodType": "monthly",
            "price": {"unit": "USD", "value": 900},
            "validFor": {"startDateTime": apidata.START},
        },
    ).json()
    response = api.post(
        f"{CATALOG}/productOffering",
        {
            "@type": "ProductOffering",
            "name": name,
            "lifecycleStatus": "Active",
            "validFor": {"startDateTime": apidata.START},
            "productSpecification": {
                "id": offering["productSpecification"]["id"],
                "@type": "ProductSpecificationRef",
            },
            "productOfferingPrice": [{"id": price["id"], "@type": "ProductOfferingPriceRef"}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def number_item(product: dict, uid: str) -> dict:
    return {
        "id": "1",
        "action": "modify",
        "product": {
            "id": product["id"],
            "@type": "ProductRefOrValue",
            "realizingResource": refs(uid),
        },
    }


@pytest.fixture
def lived(api):
    """A line added on DAY0 with one number, renumbered on day 5, suspended on day 10,
    resumed on day 12 and moved to another offering on day 15: (product, orders, numbers,
    offering)."""
    offering = apidata.sellable_offering(api)
    spec = offering["productSpecification"]
    response = api.patch(
        spec["href"],
        {"resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]},
    )
    assert response.status_code == 200, response.text
    target = same_spec_offering(api, offering, "Fiber 500")
    account = apidata.account(api)
    first = number(api, value="12025550123")
    second = number(api, value="12025550124")
    at(api, 0)
    item = apidata.add_item(offering)
    item["product"] = {"@type": "ProductRefOrValue", "realizingResource": refs(first["id"])}
    added = apidata.order(api, account, item)
    assert added["state"] == "completed", added
    product = apidata.product_of(api, added)
    orders = {"added": added["id"]}
    for key, days, body in (
        ("renumbered", 5, number_item(product, second["id"])),
        ("suspended", 10, status_item(product, "suspended")),
        ("resumed", 12, status_item(product, "active")),
        ("changed", 15, modify_item(product, target)),
    ):
        day = at(api, days)
        placed = apidata.order(
            api, account, body, requestedStartDate=timeline.iso(timeline.midnight(day))
        )
        assert placed["state"] == "completed", placed
        orders[key] = placed["id"]
    return product, orders, (first, second), target


def test_every_entry_kind_newest_first(api, lived):
    product, orders, (first, second), target = lived
    response = api.get(HISTORY.format(product["id"]))
    assert response.status_code == 200, response.text
    entries = response.json()
    assert [(e["kind"], e["date"]) for e in entries] == [
        ("offeringChange", str(DAY0 + timedelta(days=15))),
        ("statusChange", str(DAY0 + timedelta(days=12))),
        ("statusChange", str(DAY0 + timedelta(days=10))),
        ("numberAssignment", str(DAY0 + timedelta(days=5))),
        ("numberAssignment", str(DAY0)),
    ]
    change, resumed, suspended, renumbered, assigned = entries
    assert change["productOffering"] == {
        "id": target["id"],
        "href": f"{CATALOG}/productOffering/{target['id']}",
        "name": "Fiber 500",
    }
    assert change["status"] is None and change["resource"] is None
    assert (resumed["status"], resumed["cause"]) == ("active", "manual")
    assert (suspended["status"], suspended["cause"]) == ("suspended", "manual")
    assert suspended["productOffering"] is None and suspended["endDate"] is None
    assert renumbered["resource"] == {
        "id": second["id"],
        "href": second["href"],
        "kind": "msisdn",
        "value": "12025550124",
        "iccid": None,
    }
    assert renumbered["endDate"] is None
    assert assigned["resource"]["value"] == "12025550123"
    assert assigned["endDate"] == str(DAY0 + timedelta(days=5))
    assert [e["productOrder"]["id"] for e in entries] == [
        orders["changed"],
        orders["resumed"],
        orders["suspended"],
        orders["renumbered"],
        orders["added"],
    ]
    assert entries[0]["productOrder"]["href"] == f"{ORDER}/{orders['changed']}"


def test_history_pages(api, lived):
    product = lived[0]
    url = HISTORY.format(product["id"])
    page = api.get(f"{url}?limit=2")
    assert [e["kind"] for e in page.json()] == ["offeringChange", "statusChange"]
    assert 'rel="next"' in page.headers["link"]
    rest = api.get(f"{url}?offset=4&limit=2")
    assert [e["kind"] for e in rest.json()] == ["numberAssignment"]
    assert "link" not in rest.headers


def test_a_product_without_changes_has_an_empty_history(api):
    offering, account = apidata.sellable_offering(api), apidata.account(api)
    at(api, 0)
    placed = apidata.order(api, account, apidata.add_item(offering))
    product = apidata.product_of(api, placed)
    response = api.get(HISTORY.format(product["id"]))
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize(
    ("path", "status"),
    [
        (HISTORY.format(NIL), 404),
        (HISTORY.format("not-a-uuid"), 404),
        (HISTORY.format(NIL) + "?status=active", 400),
        (HISTORY.format(NIL) + "?limit=0", 400),
    ],
)
def test_history_errors(api, path, status):
    response = api.get(path)
    assert response.status_code == status, response.text


def test_each_branch_reads_its_table_by_product(db_conn):
    text = plan(
        db_conn,
        product_history.HISTORY,
        {"product": NIL, "offset": 0, "limit": 21},
        off=NO_SEQSCAN,
    )
    assert "Seq Scan" not in text, text
    for table in ("product_status_change c", "product_offering_change o", "number_assignment a"):
        assert re.search(
            rf"Index (Only )?Scan (using|on) \S+ on {table}\b|Bitmap Heap Scan on {table}\b", text
        ), text
