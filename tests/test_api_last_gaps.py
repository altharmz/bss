"""The last TMF gaps outside TMF637 (docs/decisions/tmf-api.md#the-last-gaps-2026-10-18):
TMF676 payment and refund documents, TMF678 derived attributes and the on-demand
request's relatedParty, TMF620 alteration price types. Every response is
contract-checked."""

import base64
from datetime import date, timedelta

from app.config import settings
from tests import apidata
from tests.apidata import BILL, CATALOG, RATE
from tests.test_billing_db import add, at, bill
from tests.timeline import NOW, z

REFUND = "/tmf-api/payment/v4/refund"
ON_DEMAND = "/tmf-api/customerBillManagement/v5/customerBillOnDemand"
CYCLE = "/tmf-api/customerBillManagement/v5/billCycle"
DEC1 = date(2026, 12, 1)

PARTY = {"id": "p-1", "name": "Ivan", "role": "payer", "@referredType": "Individual"}
DOCUMENTS = {
    "name": "October rent",
    "authorizationCode": "AUTH-1",
    "correlatorId": "c-42",
    "channel": {"id": "web", "name": "Web shop"},
}
METHOD = {
    "name": "card",
    "@type": "CreditCard",
    "description": "Visa ending 4242",
    "isPreferred": True,
    "status": "active",
    "statusDate": "2026-10-01T09:00:00Z",
    "validFor": {"startDateTime": "2026-01-01T00:00:00Z"},
}


def account_billed(api, db_conn, **over) -> dict:
    """An account with one product from Dec 1 and the bill of Dec 1."""
    offering = apidata.sellable_offering(api, value=300)
    account = apidata.account(api, **over)
    add(api, account, offering, DEC1, on=at(date(2026, 11, 20)))
    bill(db_conn, account, DEC1)
    return account


# --- TMF676 ---


def test_a_payment_keeps_its_documents(api):
    account = apidata.account(api)
    body = apidata.payment_body(
        account,
        description="rent",
        payer=PARTY,
        paymentMethod=METHOD,
        **DOCUMENTS,
    )
    body["account"]["description"] = "the main account"
    response = api.post(apidata.PAYMENT, body)
    assert response.status_code == 201, response.text
    payment = response.json()
    for name, value in {**DOCUMENTS, "description": "rent", "payer": PARTY}.items():
        assert payment[name] == value, name
    assert payment["paymentMethod"] == METHOD
    assert payment["account"]["description"] == "the main account"
    assert payment["account"]["id"] == account["id"]
    assert api.get(payment["href"]).json() == payment


def test_a_refund_keeps_its_documents(api):
    account = apidata.account(api)
    apidata.payment(api, account, 100)
    body = {
        "account": {"id": account["id"], "description": "the main account"},
        "totalAmount": {"unit": "USD", "value": 20},
        "paymentMethod": METHOD,
        "requestor": {**PARTY, "role": "requestor"},
        **DOCUMENTS,
    }
    response = api.post(REFUND, body)
    assert response.status_code == 201, response.text
    refund = response.json()
    assert refund["requestor"]["role"] == "requestor" and refund["name"] == "October rent"
    assert refund["paymentMethod"] == METHOD
    assert refund["account"]["description"] == "the main account"
    assert api.get(refund["href"]).json() == refund


# --- TMF678 ---


def test_a_rate_names_what_it_bills(api, db_conn):
    account = account_billed(api, db_conn)
    (rate,) = api.get(f"{RATE}?billingAccount.id={account['id']}").json()
    assert rate["name"] == "Fiber 100"
    assert rate["description"] == "Fiber 100, 2026-12-01 - 2026-12-31"
    assert rate["characteristic"] == []
    assert rate["bill"]["name"].startswith("BL-")
    assert rate["billingAccount"]["ratingType"] == "postpaid"


def test_a_bill_names_its_parties_accounts_and_document(api, db_conn):
    method = {"id": "pm-1", "name": "card", "@type": "PaymentMethodRef"}
    account = account_billed(api, db_conn, defaultPaymentMethod=method)
    (b,) = api.get(f"{BILL}?billingAccount.id={account['id']}").json()
    assert b["paymentMethod"] == method
    (party,) = b["relatedParty"]
    assert party["role"] == "customer" and party["partyOrPartyRole"]["@referredType"] == "Customer"
    assert "financialAccount" not in b  # the account is linked to none
    assert b["billCycle"]["name"] == f"Daily billing {DEC1}"
    (document,) = b["billDocument"]
    raw = base64.b64decode(document["content"])
    assert document["size"] == {"amount": len(raw), "units": "bytes"}
    assert document["id"] == b["id"]
    assert document["description"] == f"Bill {b['billNo']}, JSON detailed"


def test_a_bill_cycle_states_its_dates(api, db_conn):
    account_billed(api, db_conn)
    (cycle,) = api.get(CYCLE).json()
    on = z(at(DEC1, 0))
    assert cycle["chargeDate"] == cycle["mailingDate"] == cycle["creditDate"] == on
    due = DEC1 + timedelta(days=settings.bill_payment_term_days)
    assert cycle["paymentDueDate"] == z(at(due, 0))
    assert cycle["BillCycleSpecification"]["id"] == "monthly"


def test_an_on_demand_request_keeps_its_related_party(api):
    account = apidata.account(api)
    party = {
        "role": "requester",
        "partyOrPartyRole": {"id": "p-1", "@type": "PartyRef", "@referredType": "Individual"},
        "@type": "RelatedPartyRefOrPartyRoleRef",
    }
    body = {"billingAccount": {"id": account["id"]}, "relatedParty": party}
    response = api.post(ON_DEMAND, body)
    assert response.status_code == 201, response.text
    assert response.json()["relatedParty"] == party
    assert api.get(response.json()["href"]).json()["relatedParty"] == party


# --- TMF620 ---


def test_alteration_prices_are_records_never_billed(api, db_conn):
    api.clock.set(NOW)
    discount = api.post(
        f"{CATALOG}/productOfferingPrice",
        {
            "@type": "ProductOfferingPrice",
            "name": "Loyalty",
            "lifecycleStatus": "Active",
            "priceType": "discount",
            "price": {"unit": "USD", "value": 50},
            "validFor": {"startDateTime": apidata.START},
        },
    )
    assert discount.status_code == 201, discount.text
    assert discount.json()["priceType"] == "discount"
    offering = apidata.sellable_offering(api, value=300)
    offer = f"{CATALOG}/productOffering/{offering['id']}"
    prices = [*api.get(offer).json()["productOfferingPrice"], {"id": discount.json()["id"]}]
    assert api.patch(offer, {"productOfferingPrice": prices}).status_code == 200
    account = apidata.account(api)
    add(api, account, offering, DEC1, on=at(date(2026, 11, 20)))
    bill(db_conn, account, DEC1)
    (rate,) = api.get(f"{RATE}?billingAccount.id={account['id']}").json()
    assert rate["taxExcludedAmount"]["value"] == 300
    recurring = {"@type": "ProductOfferingPrice", "name": "x", "lifecycleStatus": "Active"}
    with_period = {**recurring, "priceType": "penalty", "recurringChargePeriodType": "monthly"}
    assert api.post(f"{CATALOG}/productOfferingPrice", with_period).status_code == 422
