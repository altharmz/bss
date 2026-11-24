"""TMF666 attributes (docs/decisions/accounts.md, "Account attributes"): every attribute
the spec defines for the account resources and the billing cycle specification is taken,
stored and returned. relatedParty holds parties in any role, one of them the anchor
(customer / owner); the descriptive attributes are documents checked against the spec;
ratingType is always postpaid; the cycle's offsets are the policy's."""

from datetime import UTC, datetime
from typing import Any

import pytest

from app.api import account, accounts, specschema
from app.config import settings
from tests import apidata
from tests.apiclient import total
from tests.apidata import ACCOUNT, NIL, ORDER
from tests.builders.tmf.api_account_attributes import CONTACT
from tests.builders.tmf.api_financial_account import FINANCIAL, financial, link, owner
from tests.builders.tmf.events import stored, subscribe

BASE = "/tmf-api/accountManagement/v5"
CYCLE = f"{BASE}/billingCycleSpecification"
JSON_PATCH = "application/json-patch+json"

TAX_EXEMPTION = [
    {
        "@type": "TaxExemptionCertificate",
        "certificateNumber": "45678889876",
        "issuingJurisdiction": "USA",
        "reason": "Embassy",
        "taxDefinition": [
            {"@type": "TaxDefinition", "id": "96", "name": "Value Added Tax", "taxType": "VAT"}
        ],
    }
]
PAYMENT_PLAN = [
    {
        "@type": "PaymentPlan",
        "numberOfPayments": 12,
        "paymentFrequency": "monthly",
        "priority": 2,
        "status": "Ineffective",
        "totalAmount": {"unit": "USD", "value": 100},
        "planType": "regular",
        "paymentMethod": {"@type": "PaymentMethodRef", "id": "6895", "name": "family payment"},
    }
]
PAYMENT_METHOD = {"@type": "PaymentMethodRef", "id": "6514", "name": "family payment"}
RELATIONSHIP = [
    {
        "@type": "AccountRelationship",
        "relationshipType": "contains",
        "account": {"@type": "AccountRef", "id": "9522", "name": "Travel Account"},
    }
]
BILL_RECEIVER = {
    "@type": "RelatedPartyRefOrPartyRoleRef",
    "role": "bill receiver",
    "partyOrPartyRole": {
        "@type": "PartyRef",
        "@referredType": "Individual",
        "id": "2186",
        "name": "Gustave Flaubert",
    },
}
DOCUMENTS = {
    "contact": CONTACT,
    "taxExemption": TAX_EXEMPTION,
    "paymentPlan": PAYMENT_PLAN,
    "defaultPaymentMethod": PAYMENT_METHOD,
    "paymentStatus": "In Arrears",
    "accountRelationship": RELATIONSHIP,
}
# date-times come back in UTC
CONTACT_OUT = [{**CONTACT[0], "validFor": {"startDateTime": "2018-06-11T00:00:00Z"}}]
DOCUMENTS_OUT = {**DOCUMENTS, "contact": CONTACT_OUT}


def _without(body: dict[str, Any], *names: str) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in names}


@pytest.mark.parametrize(
    ("schema", "taken", "documents"),
    [
        (
            "BillingAccount",
            {
                "name", "description", "accountType", "ratingType", "state", "relatedParty",
                "creditLimit", "accountBalance", "billStructure", "financialAccount",
                "lastUpdate",
            },
            account.DOCUMENTS,
        ),
        (
            "FinancialAccount",
            {
                "name", "description", "accountType", "state", "relatedParty", "creditLimit",
                "accountBalance", "accountRelationship", "lastUpdate",
            },
            accounts.FINANCIAL_DOCUMENTS,
        ),
        (
            "PartyAccount",
            {
                "name", "description", "accountType", "state", "relatedParty", "creditLimit",
                "accountBalance", "financialAccount", "lastUpdate",
            },
            accounts.PARTY_DOCUMENTS,
        ),
        (
            "SettlementAccount",
            {
                "name", "description", "accountType", "state", "relatedParty", "creditLimit",
                "accountBalance", "financialAccount", "lastUpdate",
            },
            accounts.PARTY_DOCUMENTS,
        ),
        (
            "BillingCycleSpecification",
            {
                "name", "description", "billingPeriod", "frequency", "billingDateShift",
                "chargeDateOffset", "creditDateOffset", "mailingDateOffset",
                "paymentDueDateOffset",
            },
            account.CYCLE_DOCUMENTS,
        ),
    ],
)  # fmt: skip
def test_every_spec_attribute_has_a_place(schema, taken, documents):
    """The documents are exactly the spec's attributes minus those with behaviour, in
    both the create (_FVO) and the patch (_MVO) schema."""
    assert len(set(documents)) == len(documents)
    assert not taken & set(documents)
    for suffix in ("_FVO", "_MVO"):
        names = set(specschema.properties("TMF666", schema + suffix))
        names -= {"id", "href", "@type", "@baseType", "@schemaLocation"}
        assert names == taken | set(documents), (schema, suffix)


# --- billing account ---


def test_billing_account_round_trip(api):
    cust = apidata.customer(api)
    body = apidata.account_body(cust, description="Home", accountType="residential")
    body["relatedParty"][0]["partyOrPartyRole"] = {"id": cust["id"]}  # answered resolved
    body["relatedParty"].insert(0, BILL_RECEIVER)  # in any role, before the customer too
    response = api.post(ACCOUNT, {**body, **DOCUMENTS, "ratingType": "postpaid"})
    assert response.status_code == 201, response.text
    acc = response.json()
    assert {k: acc[k] for k in DOCUMENTS_OUT} == DOCUMENTS_OUT
    assert (acc["description"], acc["accountType"], acc["ratingType"]) == (
        "Home",
        "residential",
        "postpaid",
    )
    assert [p["role"] for p in acc["relatedParty"]] == ["bill receiver", "customer"]
    assert acc["relatedParty"][0] == BILL_RECEIVER  # stored as sent, not resolved
    assert acc["relatedParty"][1]["partyOrPartyRole"] == {
        "id": cust["id"],
        "href": cust["href"],
        "name": cust["name"],
        "@type": "PartyRoleRef",
        "@referredType": "Customer",
    }
    assert api.get(acc["href"]).json() == acc
    assert api.get(f"{acc['href']}?fields=paymentPlan,ratingType").json() == {
        "id": acc["id"],
        "href": acc["href"],
        "@type": "BssBillingAccount",
        "paymentPlan": PAYMENT_PLAN,
        "ratingType": "postpaid",
    }
    assert [a["id"] for a in api.get(f"{ACCOUNT}?accountType=residential").json()] == [acc["id"]]
    # documents: a merge patch replaces one whole, null removes it
    patched = api.patch(acc["href"], {"contact": None, "paymentStatus": "paid"}).json()
    assert "contact" not in patched and patched["paymentStatus"] == "paid"


def test_an_account_from_before_0023_answers_its_customer_alone(api):
    acc = apidata.account(api)
    assert [p["role"] for p in acc["relatedParty"]] == ["customer"]
    assert "description" not in acc and "contact" not in acc
    assert acc["ratingType"] == "postpaid"


@pytest.mark.parametrize(
    ("today", "day"), [((2026, 3, 5), 5), ((2026, 3, 28), 28), ((2026, 3, 31), 28)]
)
def test_bill_cycle_day_defaults_to_the_creation_day(api, today, day):
    api.clock.set(datetime(*today, 9, 0, tzinfo=UTC))
    body = _without(apidata.account_body(apidata.customer(api)), "billCycleDay")
    response = api.post(ACCOUNT, body)
    assert response.status_code == 201, response.text
    assert response.json()["billCycleDay"] == day


def test_currency_defaults_to_the_credit_limit_else_the_default_currency(api):
    cust = apidata.customer(api)
    body = _without(apidata.account_body(cust), "currency")
    assert api.post(ACCOUNT, body).json()["currency"] == settings.ui_currency
    limited = {**body, "creditLimit": {"unit": "EUR", "value": 100}}
    assert api.post(ACCOUNT, limited).json()["currency"] == "EUR"
    contradicting = {**limited, "currency": "USD"}
    assert api.post(ACCOUNT, contradicting).status_code == 422


def test_rating_type_is_postpaid_by_default_and_cannot_be_removed(api):
    acc = apidata.account(api)
    assert acc["ratingType"] == "postpaid"
    assert api.patch(acc["href"], {"ratingType": "postpaid"}).status_code == 200
    assert api.patch(acc["href"], {"ratingType": None}).status_code == 422
    assert api.patch(acc["href"], {"ratingType": "hybrid"}).status_code == 422
    offering = apidata.sellable_offering(api)
    ref = {"id": acc["id"], "@type": "BillingAccountRef", "ratingType": "prepaid"}
    body = apidata.order_body(acc, apidata.add_item(offering), billingAccount=ref)
    assert api.post(ORDER, body).status_code == 422  # the account is postpaid
    body["billingAccount"]["ratingType"] = "postpaid"
    assert api.post(ORDER, body).status_code == 201


def test_the_customer_changes_only_while_the_account_has_no_history(api, db_conn):
    subscribe(api, "TMF666")
    acc = apidata.account(api)
    other = apidata.customer(api, apidata.individual(api, "Anna", "Petrova"), name="Anna")
    moved = apidata.account_body(other)["relatedParty"] + [BILL_RECEIVER]
    response = api.patch(acc["href"], {"relatedParty": moved})
    assert response.status_code == 200, response.text
    assert response.json()["relatedParty"][0]["partyOrPartyRole"]["id"] == other["id"]
    assert [a["id"] for a in api.get(other["href"]).json()["account"]] == [acc["id"]]
    events = [t for t, _ in stored(db_conn, "TMF666")]
    assert events[-1] == "BillingAccountAttributeValueChangeEvent"
    apidata.order(api, acc, apidata.add_item(apidata.sellable_offering(api)))
    back = apidata.account_body(apidata.customer(api))["relatedParty"]
    response = api.patch(acc["href"], {"relatedParty": back})
    assert response.status_code == 409
    assert "once the account has history" in response.json()["message"]
    # the other parties still change
    assert api.patch(acc["href"], {"relatedParty": moved[:1]}).status_code == 200
    gone = [{**moved[0], "partyOrPartyRole": {**moved[0]["partyOrPartyRole"], "id": NIL}}]
    assert api.patch(acc["href"], {"relatedParty": gone}).status_code == 409


def test_a_new_customer_must_own_the_linked_financial_account(api):
    acc = apidata.account(api)
    cust = api.get(acc["relatedParty"][0]["partyOrPartyRole"]["href"]).json()
    party = api.get(cust["engagedParty"]["href"]).json()
    fa = financial(api, party)
    assert api.patch(acc["href"], {"financialAccount": link(fa)}).status_code == 200
    other = apidata.customer(api, apidata.individual(api, "Anna", "Petrova"), name="Anna")
    moved = {"relatedParty": apidata.account_body(other)["relatedParty"]}
    response = api.patch(acc["href"], moved)
    assert response.status_code == 422
    assert "financialAccount" in response.json()["message"]
    unlinked = api.patch(acc["href"], {**moved, "financialAccount": None})
    assert unlinked.status_code == 200, unlinked.text
    assert "financialAccount" not in unlinked.json()


def test_related_party_problems(api):
    cust = apidata.customer(api)
    customer_entry = apidata.account_body(cust)["relatedParty"][0]
    for related, message in [
        ([customer_entry, customer_entry], 'role "customer" is required, not 2'),
        ([BILL_RECEIVER], "not 0 (other roles: 'bill receiver')"),
        ([customer_entry, {**BILL_RECEIVER, "zz": 1}], "relatedParty.1.zz: unknown attribute"),
        ([customer_entry, _without(BILL_RECEIVER, "role")], "relatedParty.1.role: Field"),
        (
            [{**customer_entry, "partyOrPartyRole": {"id": NIL}}],
            f"customer {NIL} does not exist",
        ),
    ]:
        body = {**apidata.account_body(cust), "relatedParty": related}
        response = api.post(ACCOUNT, body)
        assert response.status_code in (400, 422), related
        assert message in response.json()["message"], response.text
    assert api.get(ACCOUNT).json() == []


def test_json_patch_adds_a_party_as_the_spec_example_does(api):
    """TMF666's PartyAccount_update_example_JSON_Patch_request, on a billing account."""
    acc = apidata.account(api)
    ops = [{"op": "add", "path": "/relatedParty/-", "value": BILL_RECEIVER}]
    response = api.patch(acc["href"], ops, content_type=JSON_PATCH)
    assert response.status_code == 200, response.text
    assert response.json()["relatedParty"][1] == BILL_RECEIVER


# --- financial, party and settlement accounts ---


def test_financial_account_documents_and_owner_change(api):
    org, person = apidata.organization(api), apidata.individual(api)
    fa = financial(api, org, contact=CONTACT, taxExemption=TAX_EXEMPTION)
    assert (fa["contact"], fa["taxExemption"]) == (CONTACT_OUT, TAX_EXEMPTION)
    assert api.get(fa["href"]).json() == fa
    moved = api.patch(fa["href"], {"relatedParty": [owner(person), BILL_RECEIVER]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["relatedParty"][0]["partyOrPartyRole"]["id"] == person["id"]
    assert total(api.get(f"{FINANCIAL}?relatedParty.id={person['id']}"), api) == "1"
    pa = api.post(
        f"{BASE}/partyAccount",
        {"name": "p", "relatedParty": [owner(person)], "financialAccount": link(fa)},
    )
    assert pa.status_code == 201, pa.text
    response = api.patch(fa["href"], {"relatedParty": [owner(org)]})
    assert response.status_code == 409
    assert "unlink them first" in response.json()["message"]


@pytest.mark.parametrize("resource", ["partyAccount", "settlementAccount"])
def test_party_account_documents_and_owner_change(api, resource):
    org, person = apidata.organization(api), apidata.individual(api)
    fa = financial(api, org)
    structure = {
        "@type": "BillStructure",
        "cycleSpecification": {"@type": "BillingCycleSpecificationRef", "id": "monthly"},
    }
    body = {
        "name": "Payouts",
        "relatedParty": [BILL_RECEIVER, owner(org)],
        "financialAccount": link(fa),
        "billStructure": structure,
        **DOCUMENTS,
    }
    response = api.post(f"{BASE}/{resource}", body)
    assert response.status_code == 201, response.text
    acc = response.json()
    assert {k: acc[k] for k in DOCUMENTS_OUT} == DOCUMENTS_OUT
    assert acc["billStructure"] == structure  # a record: never billed
    assert acc["accountBalance"] == []
    assert api.get(acc["href"]).json() == acc
    # the owner changes; a link to the old owner's financial account must go with it
    moved = {"relatedParty": [owner(person)]}
    response = api.patch(acc["href"], moved)
    assert response.status_code == 422
    assert "financialAccount" in response.json()["message"]
    response = api.patch(acc["href"], {**moved, "financialAccount": None})
    assert response.status_code == 200, response.text
    assert response.json()["relatedParty"] == [
        {**acc["relatedParty"][1], "partyOrPartyRole": owner_ref(person)}
    ]


def owner_ref(party: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": party["id"],
        "href": party["href"],
        "name": "Ivan Ivanov",
        "@type": "PartyRef",
        "@referredType": "Individual",
    }


# --- billing cycle specification ---


def test_cycle_specification_offsets_and_valid_for(api):
    monthly = api.get(f"{CYCLE}/monthly").json()
    policy = {
        "chargeDateOffset": 0,
        "creditDateOffset": 0,
        "mailingDateOffset": 0,
        "paymentDueDateOffset": settings.bill_payment_term_days,
    }
    assert {k: monthly[k] for k in policy} == policy
    valid_for = {"startDateTime": "2026-01-01T03:00:00+03:00"}
    body = {"@type": "BillingCycleSpecification", "name": "Ours", "validFor": valid_for}
    response = api.post(CYCLE, {**body, **policy})
    assert response.status_code == 201, response.text
    spec = response.json()
    assert spec["validFor"] == {"startDateTime": "2026-01-01T00:00:00Z"}
    assert {k: spec[k] for k in policy} == policy
    assert api.get(spec["href"]).json() == spec
    for wrong in (
        {"chargeDateOffset": 1},
        {"mailingDateOffset": 25},
        {"paymentDueDateOffset": settings.bill_payment_term_days + 1},
        {"validFor": {"startDateTime": "2026-01-01"}},
    ):
        assert api.post(CYCLE, {**body, **wrong}).status_code == 422, wrong
        assert api.patch(spec["href"], wrong).status_code == 422, wrong
    assert "validFor" not in api.patch(spec["href"], {"validFor": None}).json()
