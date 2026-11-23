"""DELETE on parties, customers and TMF666 resources: only what nothing references.

The catalog's rule (tests/test_api_catalog_delete.py) for TMF632, TMF629 and TMF666: a
204 when nothing refers to the entry, else a 409 naming the references, nothing changed.
A billing account goes only while it has no history - nothing ordered, charged, billed,
paid, refunded or processed by a billing run; closing it is the way out. Writers that
refer to a row take it FOR KEY SHARE (or FOR UPDATE), deletes FOR UPDATE, and the worker
skips an account deleted after its run listed it.
"""

from datetime import date, timedelta
from uuid import UUID

import psycopg
import pytest

from app.ordering.process import ItemRequest, OrderRequest, place_order
from app.repository import billing, parties
from app.worker import db as worker_db
from app.worker.process import DateRun
from app.worker.runs import start_run
from tests import apidata, timeline
from tests.builders.api_catalog_delete import _finish, _waits, gone, refused
from tests.builders.api_financial_account import FINANCIAL, financial, link, owner
from tests.builders.billing_db import at
from tests.timeline import TZ

BASE = "/tmf-api/accountManagement/v5"
FORMAT = f"{BASE}/billFormat"
MEDIA = f"{BASE}/billPresentationMedia"
INDIVIDUAL = f"{apidata.PARTY}/individual"
ORGANIZATION = f"{apidata.PARTY}/organization"
NIL = apidata.NIL
NOV20, DEC1 = date(2026, 11, 20), date(2026, 12, 1)


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


def party_account(api, resource: str, party: dict, **over) -> dict:
    body = {"name": "Partner payouts", "relatedParty": [owner(party)], **over}
    response = api.post(f"{BASE}/{resource}", body)
    assert response.status_code == 201, response.text
    return response.json()


def created(api, path: str, body: dict) -> dict:
    response = api.post(path, body)
    assert response.status_code == 201, response.text
    return response.json()


def test_unreferenced_entries_are_deleted(api):
    person, org = apidata.individual(api), apidata.organization(api)
    cust = apidata.customer(api, apidata.individual(api, "Anna", "Petrova"))
    acc = apidata.account(api, cust)
    fa = financial(api, org)
    pa = party_account(api, "partyAccount", org)
    sa = party_account(api, "settlementAccount", org)
    fmt = created(api, FORMAT, {"name": "JSON summary"})
    media = created(api, MEDIA, {"name": "Portal"})
    for entry in (acc, cust, pa, sa, fa, person, org, fmt, media):
        response = api.delete(entry["href"])
        assert response.status_code == 204, response.text
        gone(api, entry["href"])
    paths = (
        INDIVIDUAL,
        ORGANIZATION,
        apidata.CUSTOMER,
        apidata.ACCOUNT,
        FINANCIAL,
        f"{BASE}/partyAccount",
        f"{BASE}/settlementAccount",
        FORMAT,
        MEDIA,
    )
    for path in paths:
        assert api.delete(f"{path}/{NIL}").status_code == 404
        assert api.delete(f"{path}/invalid").status_code == 404
        assert api.delete(path).status_code == 405


def test_delete_takes_no_query_parameters(api):
    org = apidata.organization(api)
    for query in ("fields=name", "force=true"):
        assert api.delete(f"{org['href']}?{query}").status_code == 400
    assert api.get(org["href"]).status_code == 200


def test_a_party_is_reachable_only_under_its_own_type(api):
    person, org = apidata.individual(api), apidata.organization(api)
    assert api.delete(f"{ORGANIZATION}/{person['id']}").status_code == 404
    assert api.delete(f"{INDIVIDUAL}/{org['id']}").status_code == 404
    pa = party_account(api, "partyAccount", org)
    assert api.delete(f"{BASE}/settlementAccount/{pa['id']}").status_code == 404
    assert api.get(pa["href"]).status_code == 200


def test_a_party_stays_while_a_customer_or_an_account_refers_to_it(api):
    person, org = apidata.individual(api), apidata.organization(api)
    cust = apidata.customer(api, person)
    refused(api, person["href"], "1 customer", "delete those first")
    fa = financial(api, org)
    pa = party_account(api, "partyAccount", org)
    sa = party_account(api, "settlementAccount", org)
    party_account(api, "settlementAccount", org, name="Second")
    refused(api, org["href"], "1 financialAccount", "1 partyAccount", "2 settlementAccount")
    for entry in (cust, fa, pa, sa):
        assert api.delete(entry["href"]).status_code == 204
    assert api.delete(person["href"]).status_code == 204
    refused(api, org["href"], "1 settlementAccount")


def test_a_customer_stays_while_it_has_billing_accounts(api):
    cust = apidata.customer(api)
    acc = apidata.account(api, cust)
    refused(api, cust["href"], "1 billingAccount", "delete them first")
    assert api.delete(acc["href"]).status_code == 204
    assert api.delete(cust["href"]).status_code == 204
    assert api.delete(cust["engagedParty"]["href"]).status_code == 204


def test_a_financial_account_stays_while_accounts_link_to_it(api):
    org = apidata.organization(api)
    fa = financial(api, org)
    acc = apidata.account(api, apidata.customer(api, org), financialAccount=link(fa))
    pa = party_account(api, "partyAccount", org, financialAccount=link(fa))
    refused(api, fa["href"], "1 billingAccount", "1 partyAccount", "unlink those accounts")
    assert api.patch(acc["href"], {"financialAccount": None}).status_code == 200
    assert api.patch(pa["href"], {"financialAccount": None}).status_code == 200
    assert api.delete(fa["href"]).status_code == 204
    assert "financialAccount" not in api.get(acc["href"]).json()


def test_formats_and_media_stay_while_an_account_prefers_them(api):
    fmt = created(api, FORMAT, {"name": "JSON summary"})
    media = created(api, MEDIA, {"name": "Portal"})
    acc = apidata.account(
        api,
        billStructure={"format": {"id": fmt["id"]}, "presentationMedia": [{"id": media["id"]}]},
    )
    refused(api, fmt["href"], "1 billingAccount", "change their billStructure first")
    refused(api, media["href"], "1 billingAccount")
    # the account's preferences go with it, so the format and the medium are free again
    assert api.delete(acc["href"]).status_code == 204
    assert api.delete(fmt["href"]).status_code == 204
    assert api.delete(media["href"]).status_code == 204


def test_a_billing_account_with_history_stays(api):
    paid = apidata.account(api)
    apidata.payment(api, paid)
    refused(api, paid["href"], "1 payment", "close it instead")
    offering = apidata.sellable_offering(api)
    api.patch(offering["href"], {"isSellable": False})
    api.clock.set(timeline.START + timedelta(hours=9))
    ordered = apidata.account(api)
    placed = apidata.order(api, ordered, apidata.add_item(offering))
    assert placed["state"] == "rejected"  # even an order that did nothing is history
    refused(api, ordered["href"], "1 productOrder")
    assert api.patch(ordered["href"], {"state": "Closed"}).status_code == 200


def test_an_account_a_billing_run_processed_stays(api, worker):
    api.clock.set(at(NOV20))
    acc = apidata.account(api)
    api.clock.set(at(DEC1, 0))
    started = start_run(worker, DEC1, now=api.clock.now(), trigger="scheduled")
    DateRun(worker, started.run, clock=api.clock, tz=TZ).run_all()
    refused(api, acc["href"], "1 billingRun")


def test_the_worker_skips_an_account_deleted_after_its_run_started(api, worker):
    api.clock.set(at(NOV20))
    kept, dropped = apidata.account(api), apidata.account(api, name="Typo")
    api.clock.set(at(DEC1, 0))
    started = start_run(worker, DEC1, now=api.clock.now(), trigger="scheduled")
    date_run = DateRun(worker, started.run, clock=api.clock, tz=TZ)
    assert len(date_run.todo) == 2
    assert api.delete(dropped["href"]).status_code == 204
    date_run.run_all()
    assert date_run.close()["status"] == "completed"
    assert list(billing.outcomes(worker, started.run["id"])) == [UUID(kept["id"])]


def test_an_account_delete_waits_for_an_order_in_flight(api, migrated_db_url):
    offering = apidata.sellable_offering(api)
    acc = apidata.account(api)
    now = timeline.START + timedelta(hours=9)
    api.clock.set(now)
    first = psycopg.connect(migrated_db_url)
    try:
        with first.transaction():
            place_order(
                first,
                OrderRequest(
                    billing_account_id=UUID(acc["id"]),
                    items=(ItemRequest("1", "add", product_offering_id=UUID(offering["id"])),),
                ),
                now=now,
                today=timeline.TOMORROW,
            )
            result = _waits(lambda: api.delete(acc["href"]))
        response = _finish(result)
    finally:
        first.close()
    assert response.status_code == 409, response.text
    assert "1 productOrder" in response.json()["message"]


def test_a_payment_waits_for_an_account_delete_in_flight(api, migrated_db_url):
    """Without the payment's lock it validates the account the delete removes, then
    fails in load_account (500) instead of answering 422."""
    acc = apidata.account(api)
    first = psycopg.connect(migrated_db_url)
    try:
        with first.transaction():
            parties.get_billing_account(first, UUID(acc["id"]), "update")
            first.execute("DELETE FROM billing_account WHERE id = %s", (UUID(acc["id"]),))
            result = _waits(lambda: api.post(apidata.PAYMENT, apidata.payment_body(acc)))
        response = _finish(result)
    finally:
        first.close()
    assert response.status_code == 422, response.text
    assert "does not exist" in response.json()["message"]


def test_a_new_customer_waits_for_a_party_delete_in_flight(api, migrated_db_url):
    """Without the customer's FOR KEY SHARE it sees the party, then fails on the foreign
    key (500) instead of answering 422."""
    person = apidata.individual(api)
    first = psycopg.connect(migrated_db_url)
    try:
        with first.transaction():
            parties.get_party(first, UUID(person["id"]), "update")
            first.execute("DELETE FROM party WHERE id = %s", (UUID(person["id"]),))
            result = _waits(lambda: api.post(apidata.CUSTOMER, apidata.customer_body(person)))
        response = _finish(result)
    finally:
        first.close()
    assert response.status_code == 422, response.text
    assert "does not exist" in response.json()["message"]


def test_a_party_delete_waits_for_a_new_customer_in_flight(api, migrated_db_url):
    person = apidata.individual(api)
    first = psycopg.connect(migrated_db_url)
    try:
        with first.transaction():
            parties.get_party(first, UUID(person["id"]), "key share")
            parties.create_customer(first, party_id=UUID(person["id"]), name="Racing")
            result = _waits(lambda: api.delete(person["href"]))
        response = _finish(result)
    finally:
        first.close()
    assert response.status_code == 409, response.text
    assert "1 customer" in response.json()["message"]


def test_mcp_deletes_with_the_write_scope_only(api, mcp):
    org = apidata.organization(api)
    reader, writer = mcp.token("reader", "read"), mcp.token("writer", "write")
    assert "delete_organization" not in mcp.tools(reader)
    response = mcp.call(reader, "delete_organization", {"party_id": org["id"]})
    assert response.json()["error"] == "insufficient_scope"
    assert api.get(org["href"]).status_code == 200
    error, answer = mcp.result(writer, "delete_organization", {"party_id": org["id"]})
    assert not error and answer == {"status": 204}
    gone(api, org["href"])
