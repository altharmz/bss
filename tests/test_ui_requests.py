"""The write requests the admin UI sends (frontend/src/api/bss.ts), replayed verbatim
against the real API and its contract checks. The UI's component tests pin these bodies
on the frontend side; this pins that the backend accepts them and stores what the UI
means. If the UI changes a body, change it here too.

Context: business date 2026-10-01 in Europe/Moscow (+03:00), the api fixture's clock.
"""

from datetime import datetime
from decimal import Decimal

import pytest

from app.worker import db as worker_db
from app.worker.queue import process_requests
from tests import apidata
from tests.apiclient import decimals
from tests.apidata import CATALOG, ORDER
from tests.test_worker_process import DEC1, DEC3, at, scenario, set_currency
from tests.timeline import TZ

OFFSET = "+03:00"
RUNS = "/internal/v1/billingRun"


def test_ui_user_management_requests(api):
    """Same bodies as Users.test.tsx and session.test.tsx, through real cookies."""
    from fastapi.testclient import TestClient

    from app.main import app

    session = "/internal/v1/session"
    users = "/internal/v1/users"
    with TestClient(app, headers={"X-Bss-Request": "1"}) as client:
        assert (
            client.post(
                session, json={"username": "i.petrova", "password": "test-password-123"}
            ).status_code
            == 200
        )
        created = client.post(
            users, json={"username": "bob", "role": "admin", "password": "initial-password"}
        )
        assert created.status_code == 201
        assert created.json()["mustChangePassword"] is True
        assert (
            client.post(
                users, json={"username": "alice", "role": "user", "password": "initial-password"}
            ).status_code
            == 201
        )
        changed = client.patch(
            users + "/alice",
            json={"role": "admin", "disabled": True},
            headers={"Content-Type": "application/json"},
        )
        assert changed.status_code == 200
        assert changed.json()["role"] == "admin"
        assert changed.json()["disabled"] is True
        assert (
            client.patch(
                users + "/alice",
                json={"password": "replacement-password"},
                headers={"Content-Type": "application/json"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                session, json={"username": "bob", "password": "initial-password"}
            ).status_code
            == 200
        )
        assert (
            client.post(
                session + "/password",
                json={"currentPassword": "initial-password", "newPassword": "replacement-password"},
            ).status_code
            == 204
        )
        assert client.get(session).status_code == 401
        assert (
            client.post(
                session, json={"username": "bob", "password": "replacement-password"}
            ).status_code
            == 200
        )
        assert client.get(session).json()["mustChangePassword"] is False
        assert client.delete(session).status_code == 204
        assert client.get(session).status_code == 401


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


def spec(api) -> dict:
    return api.post(
        f"{CATALOG}/productSpecification",
        {
            "@type": "ProductSpecification",
            "name": "Mobile Voice & Data",
            "lifecycleStatus": "Active",
        },
    ).json()


def create_offering(api, code: str = "MOB-XL", unit: str = "monthly", length: int = 1) -> dict:
    """createOffering(): the price (amount as a raw number token), then the offering."""
    valid_for = (
        '{"startDateTime":"2026-10-02T00:00:00+03:00","endDateTime":"2027-01-01T00:00:00+03:00"}'
    )
    multiplier = (
        f'"recurringChargePeriodLength":{length},' if (unit, length) != ("monthly", 1) else ""
    )
    price = api.post_raw(
        f"{CATALOG}/productOfferingPrice",
        f'{{"@type":"ProductOfferingPrice","name":"Mobile XL {unit}","lifecycleStatus":"Active",'
        f'"priceType":"recurring","recurringChargePeriodType":"{unit}",{multiplier}'
        f'"price":{{"unit":"RUB","value":1990.50}},"validFor":{valid_for}}}',
    )
    assert price.status_code == 201, price.text
    offering = api.post(
        f"{CATALOG}/productOffering",
        {
            "@type": "ProductOffering",
            "name": "Mobile XL",
            "lifecycleStatus": "In Design",
            "validFor": {
                "startDateTime": "2026-10-02T00:00:00+03:00",
                "endDateTime": "2027-01-01T00:00:00+03:00",
            },
            "productSpecification": {"id": spec(api)["id"], "@type": "ProductSpecificationRef"},
            "productOfferingPrice": [
                {"id": price.json()["id"], "@type": "ProductOfferingPriceRef"}
            ],
            "externalIdentifier": [
                {
                    "id": code,
                    "externalIdentifierType": "offeringCode",
                    "@type": "ExternalIdentifier",
                }
            ],
        },
    )
    assert offering.status_code == 201, offering.text
    return offering.json()


@pytest.mark.parametrize(
    "unit,length", [("monthly", 1), ("daily", 3), ("weekly", 2), ("monthly", 3), ("yearly", 1)]
)
def test_new_offering_is_stored_as_the_ui_means_it(api, unit, length):
    offering = create_offering(api, unit=unit, length=length)
    assert offering["lifecycleStatus"] == "In Design"
    assert offering["externalIdentifier"][0]["id"] == "MOB-XL"
    # "valid to 2026-12-31" (inclusive) is stored as the exclusive midnight of Jan 1
    assert offering["validFor"] == {
        "startDateTime": "2026-10-01T21:00:00Z",
        "endDateTime": "2026-12-31T21:00:00Z",
    }
    price = decimals(api.get(offering["productOfferingPrice"][0]["href"]))
    assert price["price"] == {"unit": "RUB", "value": Decimal("1990.50")}
    assert (price["recurringChargePeriodType"], price["recurringChargePeriodLength"]) == (
        unit,
        length,
    )


def test_lifecycle_steps_are_merge_patches(api):
    offering = create_offering(api)
    for status in ("Active", "Retired"):  # NEXT_LIFECYCLE
        response = api.patch(offering["href"], {"lifecycleStatus": status})
        assert response.status_code == 200, response.text
        assert response.json()["lifecycleStatus"] == status


def test_add_and_delete_orders_as_the_ui_sends_them(api):
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    api.clock.set(api.clock.now().replace(day=2))  # prices are valid from Oct 2
    # orderAdd(): requestedStartDate is noon of the chosen day in BILLING_TZ
    added = api.post(
        ORDER,
        {
            "@type": "ProductOrder",
            "billingAccount": {"id": account["id"], "@type": "BillingAccountRef"},
            "requestedStartDate": f"2026-10-06T12:00:00{OFFSET}",
            "productOrderItem": [
                {
                    "@type": "ProductOrderItem",
                    "id": "1",
                    "action": "add",
                    "productOffering": {"id": offering["id"], "@type": "ProductOfferingRef"},
                }
            ],
        },
    )
    assert added.status_code == 201 and added.json()["state"] == "completed", added.text
    product = apidata.product_of(api, added.json())
    started = product["startDate"]
    # midnight of 2026-10-06 in Moscow: the day the operator chose, not a day off
    assert started == "2026-10-05T21:00:00Z"

    # orderDelete(): no requestedStartDate = today; a product not started yet is cancelled
    deleted = api.post(
        ORDER,
        {
            "@type": "ProductOrder",
            "billingAccount": {"id": account["id"], "@type": "BillingAccountRef"},
            "productOrderItem": [
                {
                    "@type": "ProductOrderItem",
                    "id": "1",
                    "action": "delete",
                    "product": {"id": product["id"], "@type": "ProductRef"},
                }
            ],
        },
    )
    assert deleted.status_code == 201 and deleted.json()["state"] == "completed", deleted.text
    assert api.get(product["href"]).json()["status"] == "cancelled"
    assert TZ.key == "Europe/Moscow"  # the offsets above assume it


def test_undo_deactivation_as_the_ui_sends_it(api):
    """undoDeactivation(): the product's termination order from TMF637, then a
    cancelProductOrder with only productOrder."""
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    api.clock.set(api.clock.now().replace(day=2))
    placed = apidata.order(api, account, apidata.add_item(offering))
    product = apidata.product_of(api, placed)
    apidata.order(
        api,
        account,
        apidata.delete_item(product["id"]),
        requestedStartDate=f"2026-10-20T12:00:00{OFFSET}",
    )
    ending = api.get(product["href"]).json()
    assert ending["status"] == "pendingTerminate"
    (termination,) = [i for i in ending["productOrderItem"] if i["role"] == "termination"]
    cancelled = api.post(
        "/tmf-api/productOrderingManagement/v5/cancelProductOrder",
        {
            "@type": "CancelProductOrder",
            "productOrder": {"id": termination["orderId"], "@type": "ProductOrderRef"},
        },
    )
    assert cancelled.status_code == 201, cancelled.text
    assert api.get(product["href"]).json()["status"] == "active"


def test_price_change_as_the_ui_sends_it(api):
    """changePrice(): the amount as a raw number token, the date as a business date."""
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    api.clock.set(api.clock.now().replace(day=2))
    assert apidata.order(api, account, apidata.add_item(offering))["state"] == "completed"
    response = api.post_raw(
        f"/internal/v1/productOffering/{offering['id']}/priceChange",
        '{"amount":1390.00,"currency":"RUB","validFrom":"2026-11-01"}',
    )
    assert response.status_code == 201, response.text
    assert decimals(response)["newPrice"]["price"] == {"unit": "RUB", "value": Decimal("1390.00")}


def started_after(api, queued_at: str) -> list[dict]:
    """The runs the Billing runs screen counts as carrying a queued request out: started at
    or after its requestedAt (frontend/src/screens/BillingRuns.tsx, carriedOut)."""
    since = datetime.fromisoformat(queued_at)
    runs = api.get(f"{RUNS}?limit=14").json()
    return [r for r in runs if datetime.fromisoformat(r["startedAt"]) >= since]


def test_retry_as_the_ui_sends_it(api, worker):
    """retryRun(): a POST without a body or content type. Once the worker has carried it
    out, the retried run reads as started after the request - what the screen waits for."""
    s = scenario(api, worker, broken=True)  # x failed on Dec 1, blocked on Dec 2 and 3
    set_currency(worker, s["fragile"]["id"], "RUB")  # the fix
    api.clock.set(at(DEC3, 12))
    dec1 = s["runs"][DEC1]["id"]
    response = api.request("POST", f"{RUNS}/{dec1}/retry")
    assert response.status_code == 202, response.text
    queued_at = response.json()["request"]["requestedAt"]
    assert started_after(api, queued_at) == []  # not carried out yet: the screen polls

    api.clock.set(at(DEC3, 12, 1))  # the worker takes it within a minute
    process_requests(worker, clock=api.clock, tz=TZ)
    runs = started_after(api, queued_at)
    assert sorted(r["runDate"] for r in runs) == ["2026-12-01", "2026-12-02", "2026-12-03"]
    assert {r["status"] for r in runs} == {"completed"}  # none running: the screen stops


def test_catch_up_as_the_ui_sends_it(api, worker):
    """catchUp(): {"upTo": "YYYY-MM-DD"}, a business date. The new runs read as started
    after the request once the worker has carried it out."""
    scenario(api, worker, broken=False)  # runs Dec 1, 2, 3
    api.clock.set(at(DEC3.replace(day=5), 1))  # 01:00 on Dec 5: Dec 5 is due (00:30)
    response = api.post_raw(f"{RUNS}/catchUp", '{"upTo":"2026-12-05"}')
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["dates"] == ["2026-12-04", "2026-12-05"]
    assert started_after(api, body["request"]["requestedAt"]) == []

    api.clock.set(at(DEC3.replace(day=5), 1, 1))
    process_requests(worker, clock=api.clock, tz=TZ)
    runs = started_after(api, body["request"]["requestedAt"])
    assert [(r["runDate"], r["status"], r["trigger"]) for r in runs] == [
        ("2026-12-05", "completed", "catchUp"),
        ("2026-12-04", "completed", "catchUp"),
    ]
    # nothing is due any more: 200, no dates, nothing queued (the screen's "Nothing to catch up")
    again = api.post_raw(f"{RUNS}/catchUp", '{"upTo":"2026-12-05"}')
    assert again.status_code == 200 and again.json() == {"dates": []}


# --- bills (frontend/src/screens/Bills.test.tsx pins the same bodies) ---

PAYMENT = "/tmf-api/payment/v4/payment"
BILL = "/tmf-api/customerBillManagement/v5/customerBill"


def two_open_bills(api, db_conn) -> tuple[dict, list[dict]]:
    """An account with the 300.00 bills of Dec 1 and Jan 1, both unpaid."""
    from datetime import date

    from tests.test_billing_db import add, bill
    from tests.test_billing_db import at as at_day

    offering = apidata.sellable_offering(api, value=300)
    account = apidata.account(api)
    add(api, account, offering, date(2026, 12, 1), on=at_day(date(2026, 11, 20)))
    bill(db_conn, account, date(2026, 12, 1))
    bill(db_conn, account, date(2027, 1, 1))
    api.clock.set(at_day(date(2027, 1, 2)))
    return account, decimals(api.get(f"{BILL}?billingAccount.id={account['id']}"))


def test_payments_as_the_ui_sends_them(api, db_conn):
    """recordPayment(): totalAmount and every item amount as exact number tokens, the
    method as paymentMethod.name; without items the oldest bills are paid first."""
    account, (dec, jan) = two_open_bills(api, db_conn)
    a = account["id"]
    plain = api.post_raw(
        PAYMENT,
        f'{{"@type":"Payment","account":{{"id":"{a}","@referredType":"BillingAccount"}},'
        '"totalAmount":{"unit":"RUB","value":350.00},"paymentMethod":{"name":"bankTransfer"}}',
    )
    assert plain.status_code == 201, plain.text
    assert [i["item"]["id"] for i in plain.json()["paymentItem"]] == [dec["id"], jan["id"]]

    lettered = api.post_raw(
        PAYMENT,
        f'{{"@type":"Payment","account":{{"id":"{a}","@referredType":"BillingAccount"}},'
        '"totalAmount":{"unit":"RUB","value":100.00},"paymentMethod":{"name":"cash"},'
        f'"paymentItem":[{{"@type":"PaymentItem","item":{{"id":"{jan["id"]}",'
        '"@referredType":"CustomerBill"},"amount":{"unit":"RUB","value":100.00}}]}',
    )
    assert lettered.status_code == 201, lettered.text
    bills = decimals(api.get(f"{BILL}?billingAccount.id={a}"))
    assert [(b["state"], b["remainingAmount"]["value"]) for b in bills] == [
        ("settled", Decimal("0.00")),
        ("partiallyPaid", Decimal("150.00")),
    ]


def test_hold_and_release_as_the_ui_sends_them(api, db_conn):
    """setBillState(): a merge patch {"state": ...}; release sends the state the dialog got
    in allowedStates."""
    _, (dec, _) = two_open_bills(api, db_conn)
    held = api.patch(f"{BILL}/{dec['id']}", {"state": "onHold"})
    assert held.status_code == 200 and held.json()["state"] == "onHold"
    allowed = api.get(f"/internal/v1/customerBill/{dec['id']}").json()["allowedStates"]
    assert allowed == ["sent"]
    released = api.patch(f"{BILL}/{dec['id']}", {"state": allowed[0]})
    assert released.status_code == 200 and released.json()["state"] == "sent"


# --- subscribers (frontend/src/screens/Subscribers.test.tsx, SubscriberCard.test.tsx) ---

PARTY = "/tmf-api/partyManagement/v5"
CUSTOMERS = "/tmf-api/customer/v5/customer"
ACCOUNTS = "/tmf-api/accountManagement/v5/billingAccount"


def create_subscriber_as_the_ui_does(api, credit_limit: str | None = "1500.50") -> dict:
    """createSubscriber(): party, customer (Active, engaging it), account."""
    party = api.post_raw(
        f"{PARTY}/individual",
        '{"@type":"Individual","givenName":"Olga","familyName":"Kuznetsova"}',
    )
    assert party.status_code == 201, party.text
    customer = api.post_raw(
        CUSTOMERS,
        '{"@type":"Customer","name":"Olga Kuznetsova","status":"Active",'
        f'"engagedParty":{{"id":"{party.json()["id"]}","@type":"PartyRef",'
        '"@referredType":"Individual"}}',
    )
    assert customer.status_code == 201, customer.text
    limit = (
        "" if credit_limit is None else f',"creditLimit":{{"unit":"RUB","value":{credit_limit}}}'
    )
    account = api.post_raw(
        ACCOUNTS,
        '{"@type":"BssBillingAccount","name":"Olga Kuznetsova main","billCycleDay":15,'
        '"currency":"RUB","relatedParty":[{"@type":"RelatedPartyRefOrPartyRoleRef",'
        f'"role":"customer","partyOrPartyRole":{{"id":"{customer.json()["id"]}",'
        '"name":"Olga Kuznetsova","@type":"PartyRoleRef","@referredType":"Customer"}}]'
        f"{limit}}}",
    )
    assert account.status_code == 201, account.text
    return {"party": party.json(), "customer": customer.json(), "account": account.json()}


def test_new_subscriber_as_the_ui_creates_it(api):
    made = create_subscriber_as_the_ui_does(api)
    account = decimals(api.get(made["account"]["href"]))
    assert account["creditLimit"] == {"unit": "RUB", "value": Decimal("1500.50")}
    assert (account["billCycleDay"], account["state"]) == (15, "Active")
    assert account["relatedParty"][0]["partyOrPartyRole"]["name"] == "Olga Kuznetsova"
    assert made["customer"]["status"] == "Active"
    assert made["customer"]["engagedParty"]["@referredType"] == "Individual"
    org = api.post_raw(f"{PARTY}/organization", '{"@type":"Organization","name":"Vasilek LLC"}')
    assert org.status_code == 201, org.text


def test_edit_subscriber_as_the_ui_patches_it(api):
    """updateSubscriber(): merge patches of only the changed attributes; the credit limit as
    an exact token or null."""
    made = create_subscriber_as_the_ui_does(api)
    party = api.patch(made["party"]["href"], {"familyName": "Kuznetsova-Petrova"})
    assert party.status_code == 200 and party.json()["familyName"] == "Kuznetsova-Petrova"
    customer = api.patch(made["customer"]["href"], {"status": "Inactive"})
    assert customer.status_code == 200 and customer.json()["status"] == "Inactive"
    exact = api.request(
        "PATCH",
        made["account"]["href"],
        content='{"creditLimit":{"unit":"RUB","value":2000.10}}',
        headers={"Content-Type": "application/merge-patch+json"},
    )
    assert exact.status_code == 200, exact.text
    assert decimals(exact)["creditLimit"]["value"] == Decimal("2000.10")
    removed = api.patch(made["account"]["href"], {"state": "Suspended", "creditLimit": None})
    assert removed.status_code == 200, removed.text
    assert removed.json()["state"] == "Suspended" and "creditLimit" not in removed.json()
