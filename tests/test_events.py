"""TMF events: what each write stores in the outbox for the hubs that want it.

Every stored event is checked against its listener's request schema in the vendored
spec (tests/contract.py), so a subscriber receives exactly what the spec says.
"""

import json
from datetime import date
from typing import Any

import psycopg
import pytest

from app.events.types import BASES
from app.worker import db as worker_db
from app.worker.on_demand import process_demands
from app.worker.process import bill_date
from tests import apidata, contract
from tests.test_api_financial_account import financial, owner
from tests.test_billing_db import add, at
from tests.timeline import TZ

CALLBACK = "http://listener.test/events"


def subscribe(api, name: str, *types: str) -> dict:
    body: dict[str, Any] = {"callback": CALLBACK}
    if types:
        body["query"] = "eventType=" + ",".join(types)
    response = api.post(f"{BASES[name]}/hub", body)
    assert response.status_code == 201, response.text
    return response.json()


def stored(conn: psycopg.Connection, name: str | None = None) -> list[tuple[str, dict]]:
    """(event type, payload) of the stored events, oldest first; each payload checked
    against the spec."""
    rows = conn.execute(
        "SELECT api, event_type, payload::text FROM event"
        " WHERE %(api)s::text IS NULL OR api = %(api)s"
        " ORDER BY seq",
        {"api": name},
    ).fetchall()
    out = []
    for api_name, event_type, text in rows:
        payload = json.loads(text)
        contract.assert_valid(api_name, event_type, payload)
        assert payload["eventType"] == event_type
        out.append((event_type, payload))
    return out


def types(conn: psycopg.Connection, name: str | None = None) -> list[str]:
    return [event_type for event_type, _ in stored(conn, name)]


@pytest.fixture
def everything(api):
    """A hub without a query on every API: every event is stored."""
    for name in BASES:
        subscribe(api, name)


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


def test_no_hub_no_event(api, db_conn):
    apidata.individual(api)
    assert types(db_conn) == []


def test_party_events(api, db_conn):
    subscribe(api, "TMF632")
    person = apidata.individual(api)
    assert api.patch(person["href"], {"familyName": "Petrov"}).status_code == 200
    assert api.patch(person["href"], {"familyName": "Petrov"}).status_code == 200  # no change
    assert api.delete(person["href"]).status_code == 204
    events = stored(db_conn, "TMF632")
    assert [t for t, _ in events] == [
        "IndividualCreateEvent",
        "IndividualAttributeValueChangeEvent",
        "IndividualDeleteEvent",
    ]
    created, changed, deleted = (p["event"]["individual"] for _, p in events)
    assert created == person
    assert changed["familyName"] == "Petrov" and deleted["familyName"] == "Petrov"
    first = events[0][1]
    assert first["id"] == first["eventId"] and first["@type"] == "IndividualCreateEvent"
    assert first["eventTime"] == first["timeOccurred"]


def test_a_hub_gets_only_the_types_it_asked_for(api, db_conn):
    subscribe(api, "TMF632", "OrganizationCreateEvent")
    apidata.individual(api)
    org = apidata.organization(api)
    api.patch(org["href"], {"name": "Renamed"})
    assert types(db_conn) == ["OrganizationCreateEvent"]
    deliveries = db_conn.execute("SELECT count(*) FROM event_delivery").fetchone()[0]
    assert deliveries == 1


def test_one_delivery_per_hub_and_none_after_unsubscribing(api, db_conn):
    first, second = subscribe(api, "TMF632"), subscribe(api, "TMF632")
    subscribe(api, "TMF629")  # another API's hub gets nothing from TMF632
    apidata.organization(api)
    assert db_conn.execute("SELECT count(*) FROM event_delivery").fetchone()[0] == 2
    assert api.delete(first["href"]).status_code == 204
    states = db_conn.execute(
        "SELECT subscription_id::text, state::text FROM event_delivery ORDER BY state"
    ).fetchall()
    assert sorted(states) == sorted([(first["id"], "dropped"), (second["id"], "pending")])
    apidata.organization(api, "Second")
    assert db_conn.execute("SELECT count(*) FROM event_delivery").fetchone()[0] == 3


def payloads(conn, event_type: str, resource: str) -> list[dict]:
    return [p["event"][resource] for t, p in stored(conn) if t == event_type]


def test_customer_and_account_events(api, db_conn, everything):
    cust = apidata.customer(api)
    api.patch(cust["href"], {"status": "Active"})  # state only
    api.patch(cust["href"], {"name": "Renamed", "status": "Inactive"})  # both
    acc = apidata.account(api, cust)
    api.patch(acc["href"], {"state": "Suspended"})
    api.patch(acc["href"], {"name": "Main"})
    org = apidata.organization(api)
    fa = financial(api, org)
    api.patch(fa["href"], {"state": "Closed"})
    party = api.post(f"{BASES['TMF666']}/partyAccount", {"name": "P", "relatedParty": [owner(org)]})
    settle = api.post(
        f"{BASES['TMF666']}/settlementAccount", {"name": "S", "relatedParty": [owner(org)]}
    )
    for created in (party, settle):
        assert created.status_code == 201
        assert api.delete(created.json()["href"]).status_code == 204
    fmt = api.post(f"{BASES['TMF666']}/billFormat", {"name": "JSON summary"}).json()
    api.patch(fmt["href"], {"description": "Short"})
    media = api.post(f"{BASES['TMF666']}/billPresentationMedia", {"name": "Portal"}).json()
    assert api.delete(media["href"]).status_code == 204
    other = apidata.account(api, cust, name="Spare")
    assert api.delete(other["href"]).status_code == 204
    assert api.delete(fa["href"]).status_code == 204
    assert types(db_conn, "TMF629") == [
        "CustomerCreateEvent",
        "CustomerStateChangeEvent",
        "CustomerStateChangeEvent",
        "CustomerAttributeValueChangeEvent",
    ]
    assert types(db_conn, "TMF666") == [
        "BillingAccountCreateEvent",
        "BillingAccountStateChangeEvent",
        "BillingAccountAttributeValueChangeEvent",
        "FinancialAccountCreateEvent",
        "FinancialAccountStateChangeEvent",
        "PartyAccountCreateEvent",
        "SettlementAccountCreateEvent",
        "PartyAccountDeleteEvent",
        "SettlementAccountDeleteEvent",
        "BillFormatCreateEvent",
        "BillFormatAttributeValueChangeEvent",
        "BillPresentationMediaCreateEvent",
        "BillPresentationMediaDeleteEvent",
        "BillingAccountCreateEvent",
        "BillingAccountDeleteEvent",
        "FinancialAccountDeleteEvent",
    ]
    (suspended,) = payloads(db_conn, "BillingAccountStateChangeEvent", "billingAccount")
    assert suspended["state"] == "Suspended" and suspended["id"] == acc["id"]


def test_catalog_events(api, db_conn, everything):
    spec = api.post(
        f"{BASES['TMF620']}/productSpecification",
        {"name": "Fiber", "lifecycleStatus": "In Design"},
    ).json()
    api.patch(spec["href"], {"lifecycleStatus": "Active"})
    offering = apidata.sellable_offering(api)
    price_href = offering["productOfferingPrice"][0]["href"]
    api.patch(price_href, {"name": "Monthly fee"})
    api.patch(offering["href"], {"description": "Fast"})
    category = api.post(f"{BASES['TMF620']}/category", {"name": "Internet"}).json()
    catalog = api.post(
        f"{BASES['TMF620']}/productCatalog", {"name": "Home", "category": [{"id": category["id"]}]}
    ).json()
    api.patch(catalog["href"], {"lifecycleStatus": "Active"})
    assert api.delete(catalog["href"]).status_code == 204
    assert api.delete(category["href"]).status_code == 204
    exported = api.post(f"{BASES['TMF620']}/exportJob", {"@type": "ExportJob"}).json()
    api.post(f"{BASES['TMF620']}/importJob", {"url": exported["url"]})
    assert api.delete(spec["href"]).status_code == 204
    seen = types(db_conn, "TMF620")
    assert seen[:2] == ["ProductSpecificationCreateEvent", "ProductSpecificationStateChangeEvent"]
    for expected in (
        "ProductOfferingPriceCreateEvent",
        "ProductOfferingCreateEvent",
        "ProductOfferingPriceAttributeValueChangeEvent",
        "ProductOfferingAttributeValueChangeEvent",
        "CategoryCreateEvent",
        "ProductCatalogCreateEvent",
        "ProductCatalogStateChangeEvent",
        "ProductCatalogDeleteEvent",
        "CategoryDeleteEvent",
        "ExportJobCreateEvent",
        "ImportJobCreateEvent",
        "ProductSpecificationDeleteEvent",
    ):
        assert expected in seen, expected
    assert "ProductOfferingStateChangeEvent" not in seen  # only the description changed


def test_order_product_payment_and_bill_events(api, db_conn, everything, worker):
    api.clock.set(at(date(2026, 10, 1)))
    offering = apidata.sellable_offering(api, value=300)
    acc = apidata.account(api)
    product = add(api, acc, offering, date(2026, 10, 2), on=at(date(2026, 10, 2)))
    later = add(api, acc, offering, date(2026, 12, 1), on=at(date(2026, 10, 2)))
    api.clock.set(at(date(2026, 11, 1), 0))
    bill_date(worker, date(2026, 11, 1), clock=api.clock, tz=TZ, trigger="scheduled")
    api.clock.set(at(date(2026, 11, 2)))
    apidata.payment(api, acc, 1000)
    order = next(r for r in later["productOrderItem"] if r["role"] == "creation")
    cancelled = api.post(
        f"{BASES['TMF622']}/cancelProductOrder", {"productOrder": {"id": order["orderId"]}}
    )
    assert cancelled.status_code == 201, cancelled.text
    refused = apidata.order(api, acc, {**apidata.add_item(offering), "action": "modify"})
    assert api.delete(refused["href"]).status_code == 204
    api.post(f"{BASES['TMF678']}/customerBillOnDemand", {"billingAccount": {"id": acc["id"]}})
    api.post(
        f"{BASES['TMF676']}/refund",
        {
            "account": {"id": acc["id"]},
            "totalAmount": {"unit": "RUB", "value": 10},
            "paymentMethod": {"@type": "Cash"},
        },
    )
    assert types(db_conn, "TMF622") == [
        "ProductOrderCreateEvent",
        "ProductOrderCreateEvent",
        "CancelProductOrderCreateEvent",
        "ProductOrderStateChangeEvent",
        "ProductOrderCreateEvent",
        "ProductOrderDeleteEvent",
    ]
    assert types(db_conn, "TMF637") == [
        "ProductCreateEvent",
        "ProductCreateEvent",
        "ProductStateChangeEvent",  # the cancelled order's product
    ]
    (gone,) = payloads(db_conn, "ProductStateChangeEvent", "product")
    assert gone["id"] == later["id"] and gone["status"] == "cancelled"
    assert types(db_conn, "TMF676") == ["PaymentCreateEvent", "RefundCreateEvent"]
    v4 = stored(db_conn, "TMF676")[0][1]
    assert set(v4) == {"eventId", "eventTime", "eventType", "event"}  # v4: no @type
    bills = types(db_conn, "TMF678")
    assert bills[0] == "CustomerBillCreateEvent"
    assert "CustomerBillStateChangeEvent" in bills  # the payment settled it
    assert bills[-1] == "CustomerBillOnDemandCreateEvent"
    (issued,) = payloads(db_conn, "CustomerBillCreateEvent", "customerBill")
    assert issued["state"] == "sent"  # the bill as issued, not its internal steps
    assert product["id"] in {p["id"] for p in payloads(db_conn, "ProductCreateEvent", "product")}


def test_worker_events_flips_and_on_demand(api, db_conn, everything, worker):
    offering = apidata.sellable_offering(api, value=300)
    acc = apidata.account(api)
    product = add(api, acc, offering, date(2026, 11, 1), on=at(date(2026, 10, 20)))
    api.clock.set(at(date(2026, 11, 1), 0))
    bill_date(worker, date(2026, 11, 1), clock=api.clock, tz=TZ, trigger="scheduled")
    flipped = payloads(db_conn, "ProductStateChangeEvent", "product")
    assert [(p["id"], p["status"]) for p in flipped] == [(product["id"], "active")]
    api.clock.set(at(date(2026, 11, 5)))
    api.post(f"{BASES['TMF678']}/customerBillOnDemand", {"billingAccount": {"id": acc["id"]}})
    api.clock.set(at(date(2026, 11, 6), 0))
    bill_date(worker, date(2026, 11, 6), clock=api.clock, tz=TZ, trigger="scheduled")
    process_demands(worker, clock=api.clock, tz=TZ)
    (finished,) = payloads(db_conn, "CustomerBillOnDemandStateChangeEvent", "customerBillOnDemand")
    assert finished["state"] in {"done", "rejected"}


def test_event_ids_are_unique_and_payloads_are_exact_json(api, db_conn, everything):
    acc = apidata.account(api)
    response = api.post_raw(
        apidata.PAYMENT,
        f'{{"@type":"Payment","account":{{"id":"{acc["id"]}"}},'
        '"totalAmount":{"unit":"RUB","value":19.90},'
        '"paymentMethod":{"name":"bankTransfer","@type":"BankAccountTransfer"}}',
    )
    assert response.status_code == 201, response.text
    ids = [p.get("id", p["eventId"]) for _, p in stored(db_conn)]
    assert len(ids) == len(set(ids))
    text = db_conn.execute(
        "SELECT payload::text FROM event WHERE event_type = 'PaymentCreateEvent'"
    ).fetchone()[0]
    assert '"value": 19.90' in text  # an exact decimal, as the API sends it
