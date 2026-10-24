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
from tests.test_api_transfer import pair
from tests.test_prepaid_command_api import failed
from tests.test_prepaid_orders import purchase, shop
from tests.test_prepaid_renewal_producer import tick
from tests.test_worker_process import DEC1, DEC3, at, scenario, set_currency
from tests.timeline import TZ

OFFSET = "+03:00"
RUNS = "/internal/v1/billingRun"


def test_ui_number_inventory_and_orders(api):
    """Exact inventory/add/modify bodies pinned in Numbers.test.tsx."""
    url = "/tmf-api/resourceInventory/v5/resource"
    numbers = []
    for value in ("12025550123", "12025550124"):
        created = api.post(
            url,
            {
                "@type": "LogicalResource",
                "name": f"MSISDN {value}",
                "value": value,
                "administrativeState": "unlocked",
                "resourceSpecification": {"id": "msisdn", "@type": "ResourceSpecificationRef"},
            },
        )
        assert created.status_code == 201, created.text
        numbers.append(created.json())
    first, second = numbers
    assert api.patch(first["href"], {"administrativeState": "locked"}).status_code == 200
    assert api.patch(first["href"], {"administrativeState": "unlocked"}).status_code == 200
    offering = apidata.sellable_offering(api)
    assert (
        api.patch(
            offering["productSpecification"]["href"],
            {"resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]},
        ).status_code
        == 200
    )
    account = apidata.account(api)

    def product_ref(number):
        return {
            "@type": "ProductRefOrValue",
            "realizingResource": [
                {"id": number["id"], "@type": "ResourceRef", "@referredType": "LogicalResource"}
            ],
        }

    order = api.post(
        ORDER,
        {
            "@type": "ProductOrder",
            "billingAccount": {"id": account["id"], "@type": "BillingAccountRef"},
            "requestedStartDate": "2026-10-02T12:00:00+03:00",
            "productOrderItem": [
                {
                    "@type": "ProductOrderItem",
                    "id": "1",
                    "action": "add",
                    "productOffering": {"id": offering["id"], "@type": "ProductOfferingRef"},
                    "product": product_ref(first),
                }
            ],
        },
    )
    assert order.status_code == 201, order.text
    assert order.json()["state"] == "completed", order.text
    product = apidata.product_of(api, order.json())
    api.clock.set(datetime.fromisoformat("2026-10-02T09:00:00+00:00"))
    changed = api.post(
        ORDER,
        {
            "@type": "ProductOrder",
            "billingAccount": {"id": account["id"], "@type": "BillingAccountRef"},
            "productOrderItem": [
                {
                    "@type": "ProductOrderItem",
                    "id": "1",
                    "action": "modify",
                    "product": {**product_ref(second), "id": product["id"]},
                }
            ],
        },
    )
    assert changed.status_code == 201, changed.text
    assert changed.json()["state"] == "completed", changed.text
    lookup = api.get(f"{apidata.PRODUCT}?realizingResource.id={second['id']}").json()
    assert [p["id"] for p in lookup] == [product["id"]]


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
        f'"price":{{"unit":"USD","value":1990.50}},"validFor":{valid_for}}}',
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
    assert price["price"] == {"unit": "USD", "value": Decimal("1990.50")}
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
        '{"amount":1390.00,"currency":"USD","validFrom":"2026-11-01"}',
    )
    assert response.status_code == 201, response.text
    assert decimals(response)["newPrice"]["price"] == {"unit": "USD", "value": Decimal("1390.00")}


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
    set_currency(worker, s["fragile"]["id"], "USD")  # the fix
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


def test_refund_and_bill_request_as_the_ui_sends_them(api):
    """AccountActions pins the same exact decimal and typed reference bodies."""
    account = apidata.account(api)
    a = account["id"]
    assert api.post(PAYMENT, apidata.payment_body(account, 100)).status_code == 201
    refund = api.post_raw(
        "/tmf-api/payment/v4/refund",
        f'{{"@type":"Refund","account":{{"id":"{a}","@referredType":"BillingAccount"}},'
        '"totalAmount":{"unit":"USD","value":25.10},'
        '"paymentMethod":{"name":"bankTransfer"},"description":"Returned deposit"}',
    )
    assert refund.status_code == 201, refund.text
    assert decimals(refund)["totalAmount"]["value"] == Decimal("25.10")
    assert refund.json()["description"] == "Returned deposit"
    assert refund.json()["paymentMethod"]["name"] == "bankTransfer"
    assert refund.json()["status"] == "done"
    assert "payment" not in refund.json()
    demands = "/tmf-api/customerBillManagement/v5/customerBillOnDemand"
    body = (
        '{"@type":"CustomerBillOnDemand","billingAccount":'
        f'{{"id":"{a}","@referredType":"BillingAccount"}}}}'
    )
    queued = api.post_raw(demands, body)
    assert queued.status_code == 201, queued.text
    assert queued.json()["billingAccount"]["id"] == a
    assert queued.json()["state"] == "inProgress"
    assert api.post_raw(demands, body).status_code == 409
    assert api.get(f"{demands}?billingAccount.id={a}").json() == [queued.json()]


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
        '"totalAmount":{"unit":"USD","value":350.00},"paymentMethod":{"name":"bankTransfer"}}',
    )
    assert plain.status_code == 201, plain.text
    assert [i["item"]["id"] for i in plain.json()["paymentItem"]] == [dec["id"], jan["id"]]

    lettered = api.post_raw(
        PAYMENT,
        f'{{"@type":"Payment","account":{{"id":"{a}","@referredType":"BillingAccount"}},'
        '"totalAmount":{"unit":"USD","value":100.00},"paymentMethod":{"name":"cash"},'
        f'"paymentItem":[{{"@type":"PaymentItem","item":{{"id":"{jan["id"]}",'
        '"@referredType":"CustomerBill"},"amount":{"unit":"USD","value":100.00}}]}',
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
        "" if credit_limit is None else f',"creditLimit":{{"unit":"USD","value":{credit_limit}}}'
    )
    account = api.post_raw(
        ACCOUNTS,
        '{"@type":"BssBillingAccount","name":"Olga Kuznetsova main","billCycleDay":15,'
        '"currency":"USD","relatedParty":[{"@type":"RelatedPartyRefOrPartyRoleRef",'
        f'"role":"customer","partyOrPartyRole":{{"id":"{customer.json()["id"]}",'
        '"name":"Olga Kuznetsova","@type":"PartyRoleRef","@referredType":"Customer"}}]'
        f"{limit}}}",
    )
    assert account.status_code == 201, account.text
    return {"party": party.json(), "customer": customer.json(), "account": account.json()}


def test_new_subscriber_as_the_ui_creates_it(api):
    made = create_subscriber_as_the_ui_does(api)
    account = decimals(api.get(made["account"]["href"]))
    assert account["creditLimit"] == {"unit": "USD", "value": Decimal("1500.50")}
    assert (account["billCycleDay"], account["state"]) == (15, "Active")
    assert account["relatedParty"][0]["partyOrPartyRole"]["name"] == "Olga Kuznetsova"
    assert made["customer"]["status"] == "Active"
    assert made["customer"]["engagedParty"]["@referredType"] == "Individual"
    org = api.post_raw(f"{PARTY}/organization", '{"@type":"Organization","name":"Vasilek LLC"}')
    assert org.status_code == 201, org.text


def test_new_prepaid_subscriber_as_the_ui_creates_it(api):
    """createSubscriber() with the prepaid mode: ratingType in the body, no credit limit; the
    card then reads the account's bucket by partyAccount.id (TMF654)."""
    party = api.post_raw(
        f"{PARTY}/individual", '{"@type":"Individual","givenName":"Olga","familyName":"K"}'
    )
    customer = api.post_raw(
        CUSTOMERS,
        '{"@type":"Customer","name":"Olga K","status":"Active",'
        f'"engagedParty":{{"id":"{party.json()["id"]}","@type":"PartyRef",'
        '"@referredType":"Individual"}}',
    )
    assert customer.status_code == 201, customer.text
    account = api.post_raw(
        ACCOUNTS,
        '{"@type":"BssBillingAccount","name":"Olga K main","billCycleDay":1,"currency":"USD",'
        '"ratingType":"prepaid","relatedParty":[{"@type":"RelatedPartyRefOrPartyRoleRef",'
        f'"role":"customer","partyOrPartyRole":{{"id":"{customer.json()["id"]}",'
        '"name":"Olga K","@type":"PartyRoleRef","@referredType":"Customer"}}]}',
    )
    assert account.status_code == 201, account.text
    assert account.json()["ratingType"] == "prepaid"
    buckets = api.get(
        "/tmf-api/prepayBalanceManagement/v4/bucket"
        f"?partyAccount.id={account.json()['id']}&offset=0&limit=100"
    )
    assert buckets.status_code == 200 and len(buckets.json()) == 1
    bucket = decimals(buckets)[0]
    assert (bucket["remainingValue"]["amount"], bucket["reservedValue"]["amount"]) == (0, 0)


def test_prepaid_money_forms_as_the_ui_sends_them(api):
    """balanceActionRequest() (PrepaidMoney.tsx): the bodies pinned in SubscriberCard.test.tsx,
    each under an Idempotency-Key. A retry of the same submission replays; a debit above the
    spendable funds is a stored refusal (201, failed) the form shows as the reason."""
    prepay = "/tmf-api/prepayBalanceManagement/v4"
    account = apidata.account(api, ratingType="prepaid")
    found = api.get(f"{prepay}/bucket?partyAccount.id={account['id']}&offset=0&limit=100")
    bucket = found.json()[0]
    owner = f'"partyAccount":{{"id":"{account["id"]}","@referredType":"BillingAccount"}}'
    where = f'"bucket":{{"id":"{bucket["id"]}"}}'

    def post(path, text, key):
        return api.request(
            "POST",
            f"{prepay}/{path}",
            content=text,
            headers={"Content-Type": "application/json", "Idempotency-Key": key},
        )

    def remaining():
        return decimals(api.get(f"{prepay}/bucket/{bucket['id']}"))["remainingValue"]["amount"]

    topup = (
        '{"@type":"TopupBalance","amount":{"amount":1250.50,"units":"USD"},'
        f'"usageType":"monetary",{where},{owner},"paymentMethod":{{"id":"cash","name":"cash"}},'
        '"reason":"Cash at the desk"}'
    )
    first = post("topupBalance", topup, "ui-topup-1")
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "completed"
    again = post("topupBalance", topup, "ui-topup-1")  # the uncertain-outcome retry
    assert again.status_code == 201 and again.json()["id"] == first.json()["id"]
    assert remaining() == Decimal("1250.50")

    adjust = (
        '{"@type":"AdjustBalance","amount":{"amount":%s,"units":"USD"},"usageType":"monetary",%s}'
    )
    credit = post("adjustBalance", adjust % ("12.50", where), "ui-adjust-1")
    debit = post("adjustBalance", adjust % ("-12.50", where), "ui-adjust-2")
    assert (credit.status_code, debit.status_code) == (201, 201), (credit.text, debit.text)
    assert (credit.json()["status"], debit.json()["status"]) == ("completed", "completed")
    assert remaining() == Decimal("1250.50")

    hold = post(
        "reserveBalance",
        '{"@type":"ReserveBalance","amount":{"amount":20.00,"units":"USD"},'
        f'"usageType":"monetary",{where},{owner},'
        '"validFor":{"endDateTime":"2026-10-06T00:00:00+03:00"}}',
        "ui-hold-1",
    )
    assert hold.status_code == 201, hold.text
    assert hold.json()["status"] == "created"
    assert remaining() == Decimal("1230.50")

    refused = post("adjustBalance", adjust % ("-5000.00", where), "ui-adjust-3")
    assert refused.status_code == 201, refused.text
    assert refused.json()["status"] == "failed" and refused.json()["reason"]
    assert remaining() == Decimal("1230.50")


def test_prepaid_transfer_as_the_ui_sends_it(api, db_conn):
    """transferRequest() and findReceiver() (api/transfer.ts), pinned in SubscriberCard.test.tsx:
    the receiver is found by number (inventory value filter, the product's account, that
    account's bucket); the exact body is accepted under an Idempotency-Key and a retry
    replays; the card then polls until the worker completes it. A refusal is stored with
    its explanation in `description`, while `reason` keeps the operator's own words."""
    tx, rx = pair(api, db_conn)
    prepay = "/tmf-api/prepayBalanceManagement/v4"
    found = api.get("/tmf-api/resourceInventory/v5/resource?value=79991234001&offset=0&limit=100")
    assert [n["id"] for n in found.json()] == [str(rx.number)]
    products = api.get(
        f"/tmf-api/productInventory/v5/product?realizingResource.id={rx.number}&offset=0&limit=100"
    )
    assert [p["billingAccount"]["id"] for p in products.json()] == [rx.id]
    own = api.get(
        f"/tmf-api/productInventory/v5/product?billingAccount.id={tx.id}&status=active"
        "&offset=0&limit=100"
    )
    assert [r["id"] for p in own.json() for r in p["realizingResource"]] == [str(tx.number)]

    def body(amount):
        return (
            '{"@type":"TransferBalance","reason":"Customer asked",'
            '"channel":{"id":"admin-ui","name":"Admin UI"},'
            f'"logicalResource":[{{"id":"{tx.number}"}}],'
            f'"receiverLogicalResource":{{"id":"{rx.number}"}},'
            f'"amount":{{"amount":{amount},"units":"USD"}},"usageType":"monetary",'
            f'"bucket":{{"id":"{tx.bucket}"}},"receiverBucket":{{"id":"{rx.bucket}"}},'
            '"receiverBucketUsageType":"monetary"}'
        )

    def post(text, key):
        return api.request(
            "POST",
            f"{prepay}/transferBalance",
            content=text,
            headers={"Content-Type": "application/json", "Idempotency-Key": key},
        )

    first = post(body("30.00"), "ui-transfer-1")
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "created"
    again = post(body("30.00"), "ui-transfer-1")  # the uncertain-outcome retry
    assert again.status_code == 201 and again.json()["id"] == first.json()["id"]
    watched = f"{prepay}/transferBalance/{first.json()['id']}"
    assert api.get(watched).json()["status"] == "created"
    tick(db_conn, api.clock.now())
    assert api.get(watched).json()["status"] == "completed"

    refused = post(body("5000.00"), "ui-transfer-2")  # more than the sender can spend
    assert refused.status_code == 201, refused.text
    stored = refused.json()
    assert stored["status"] == "failed" and stored["reason"] == "Customer asked"
    assert stored["description"] and stored["description"] != stored["reason"]


def test_balance_action_history_reads_as_the_ui_makes_them(api, db_conn):
    """loadBalanceActions() / loadBalanceAction() (api/history.ts), pinned in
    SubscriberCard.test.tsx:
    the sender's card asks by partyAccount.id, the receiver's also by receiverBucket.id (the
    transfer it received), the receiving account is named through its bucket, and one action
    is fetched by id for the detail dialog."""
    tx, rx = pair(api, db_conn)
    prepay = "/tmf-api/prepayBalanceManagement/v4"
    moved = api.post(
        f"{prepay}/transferBalance",
        {
            "@type": "TransferBalance",
            "reason": "Customer asked",
            "channel": {"id": "admin-ui", "name": "Admin UI"},
            "logicalResource": [{"id": str(tx.number)}],
            "receiverLogicalResource": {"id": str(rx.number)},
            "amount": {"amount": 10, "units": "USD"},
            "usageType": "monetary",
            "bucket": {"id": tx.bucket},
            "receiverBucket": {"id": rx.bucket},
            "receiverBucketUsageType": "monetary",
        },
        headers={"Idempotency-Key": "ui-history-1"},
    ).json()
    assert "id" in moved, moved
    history = f"{prepay}/balanceActionHistory"
    sent = api.get(f"{history}?partyAccount.id={tx.id}&offset=0&limit=100")
    assert [a["id"] for a in sent.json()] == [moved["id"]]
    assert sent.json()[0]["@type"] == "TransferBalance"
    assert sent.json()[0]["receiverBucket"]["id"] == rx.bucket
    owner = api.get(f"{prepay}/bucket/{rx.bucket}").json()["partyAccount"]
    assert owner["id"] == rx.id and owner["name"]
    got = api.get(f"{history}?receiverBucket.id={rx.bucket}&offset=0&limit=100")
    assert [a["id"] for a in got.json()] == [moved["id"]]
    assert got.json()[0]["partyAccount"]["id"] == tx.id  # the sender, named for the receiver
    assert api.get(f"{history}?receiverBucket.id={tx.bucket}&offset=0&limit=100").json() == []
    one = api.get(f"{history}/{moved['id']}").json()
    assert one["transferCost"]["value"] == 0 and one["costOwner"] == "originator"
    assert one["status"] == "created" and one["requestedDate"]


def test_prepaid_order_and_command_reads_as_the_ui_makes_them(api, db_conn, monkeypatch):
    """OrderWatch / FailedCommands (api/commands.ts): a prepaid add answers `inProgress`, the UI
    follows it through the command that has the order's id, lists an account's technically
    failed commands with the query it sends, and retries one with a POST without a body; a
    second retry is the 409 it handles by looking again."""
    off, account = shop(api, db_conn)
    order = purchase(api, off, account)
    assert order["state"] == "inProgress"
    command = api.get(f"/internal/v1/prepaid/command/{order['id']}").json()
    assert (command["id"], command["kind"], command["state"]) == (order["id"], "order", "pending")
    # due today: the card's OrderWatch calls anything later "scheduled"
    today = api.get("/internal/v1/context").json()["businessDate"]
    assert command["needs_topup"] is None and command["dueDate"] == today

    _, _, stuck = failed(db_conn, monkeypatch)
    owner = db_conn.execute(
        "SELECT billing_account_id FROM prepaid_command WHERE id = %s", (stuck["id"],)
    ).fetchone()[0]
    listed = api.get(
        f"/internal/v1/prepaid/command?billingAccount.id={owner}&state=failedTechnical"
        "&offset=0&limit=100"
    )
    assert [c["id"] for c in listed.json()] == [str(stuck["id"])]
    assert listed.json()[0]["error"] and listed.json()[0]["nextRetryAt"]
    assert (
        api.get(
            f"/internal/v1/prepaid/command?billingAccount.id={account['id']}&state=failedTechnical"
        ).json()
        == []
    )
    retried = api.request("POST", f"/internal/v1/prepaid/command/{stuck['id']}/retry")
    assert retried.status_code == 202 and retried.json()["state"] == "pending"
    again = api.request("POST", f"/internal/v1/prepaid/command/{stuck['id']}/retry")
    assert again.status_code == 409


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
        content='{"creditLimit":{"unit":"USD","value":2000.10}}',
        headers={"Content-Type": "application/merge-patch+json"},
    )
    assert exact.status_code == 200, exact.text
    assert decimals(exact)["creditLimit"]["value"] == Decimal("2000.10")
    removed = api.patch(made["account"]["href"], {"state": "Suspended", "creditLimit": None})
    assert removed.status_code == 200, removed.text
    assert removed.json()["state"] == "Suspended" and "creditLimit" not in removed.json()
