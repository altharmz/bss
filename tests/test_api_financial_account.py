"""TMF666 financialAccount: owner, per-currency balance of the linked billing accounts,
derived accountRelationship, and the billing account's financialAccount link."""

from decimal import Decimal

import pytest
from psycopg import errors

from tests import apidata
from tests.apiclient import decimals
from tests.apidata import ACCOUNT, NIL, PARTY

FINANCIAL = "/tmf-api/accountManagement/v5/financialAccount"


def owner(party, **over):
    return {
        "role": "owner",
        "partyOrPartyRole": {
            "id": party["id"],
            "href": party["href"],
            "@type": "PartyRef",
            "@referredType": party["@type"],
        },
        "@type": "RelatedPartyRefOrPartyRoleRef",
        **over,
    }


def financial(api, party, **over):
    body = {
        "@type": "FinancialAccount",
        "name": "Group receivables",
        "relatedParty": [owner(party)],
    }
    response = api.post(FINANCIAL, {**body, **over})
    assert response.status_code == 201, response.text
    assert response.headers["location"] == response.json()["href"]
    return response.json()


def link(fa):
    return {"id": fa["id"], "@type": "FinancialAccountRef", "@referredType": "FinancialAccount"}


def test_financial_account_round_trip(api):
    org = apidata.organization(api)
    fa = financial(api, org, description="All lines", accountType="Business")
    assert fa == {
        "id": fa["id"],
        "href": f"{FINANCIAL}/{fa['id']}",
        "name": "Group receivables",
        "description": "All lines",
        "accountType": "Business",
        "state": "Active",
        "relatedParty": [
            {
                "role": "owner",
                "partyOrPartyRole": {
                    "id": org["id"],
                    "href": org["href"],
                    "name": "Romashka LLC",
                    "@type": "PartyRef",
                    "@referredType": "Organization",
                },
                "@type": "RelatedPartyRefOrPartyRoleRef",
            }
        ],
        "accountBalance": [],
        "accountRelationship": [],
        "lastUpdate": fa["lastUpdate"],
        "@type": "FinancialAccount",
    }
    assert api.get(fa["href"]).json() == fa
    assert [f["id"] for f in api.get(FINANCIAL).json()] == [fa["id"]]


def test_balance_is_per_currency_sum_of_linked_billing_accounts(api):
    org = apidata.organization(api)
    fa = financial(api, org)
    cust = apidata.customer(api, org)
    rub1 = apidata.account(api, cust, name="RUB 1", financialAccount=link(fa))
    rub2 = apidata.account(api, cust, name="RUB 2")
    eur = apidata.account(api, cust, name="EUR", currency="EUR", financialAccount=link(fa))
    assert rub1["financialAccount"] == {
        "id": fa["id"],
        "href": fa["href"],
        "name": fa["name"],
        "@type": "FinancialAccountRef",
        "@referredType": "FinancialAccount",
    }
    assert "financialAccount" not in rub2
    assert api.patch(rub2["href"], {"financialAccount": link(fa)}).status_code == 200
    apidata.payment(api, rub1, 100)
    apidata.payment(api, rub2, 20.5)
    apidata.payment(api, eur, 7, unit="EUR")
    apidata.payment(api, apidata.account(api, cust, name="unlinked"), 1000)
    body = decimals(api.get(fa["href"]))
    assert body["accountBalance"] == [
        {
            "balanceType": "currentBalance",
            "amount": {"unit": "EUR", "value": Decimal("7.00")},
            "@type": "AccountBalance",
        },
        {
            "balanceType": "currentBalance",
            "amount": {"unit": "RUB", "value": Decimal("120.50")},
            "@type": "AccountBalance",
        },
    ]
    assert [
        (r["relationshipType"], r["account"]["id"], r["account"]["@referredType"])
        for r in body["accountRelationship"]
    ] == [
        ("contains", rub1["id"], "BillingAccount"),
        ("contains", rub2["id"], "BillingAccount"),
        ("contains", eur["id"], "BillingAccount"),
    ]
    assert body["accountRelationship"][0]["account"]["@type"] == "AccountRef"
    linked = api.get(f"{ACCOUNT}?financialAccount.id={fa['id']}").json()
    assert [a["name"] for a in linked] == ["RUB 1", "RUB 2", "EUR"]
    unlinked = api.patch(rub1["href"], {"financialAccount": None}).json()
    assert "financialAccount" not in unlinked
    rub = decimals(api.get(fa["href"]))["accountBalance"][1]["amount"]
    assert rub == {"unit": "RUB", "value": Decimal("20.50")}


def test_link_needs_the_same_owner_and_an_open_financial_account(api):
    org, other = apidata.organization(api), apidata.organization(api, "Other LLC")
    fa, foreign = financial(api, org), financial(api, other)
    cust = apidata.customer(api, org)
    body = apidata.account_body(cust, financialAccount=link(foreign))
    response = api.post(ACCOUNT, body)
    assert response.status_code == 422 and "same party" in response.json()["message"]
    assert api.get(ACCOUNT).json() == []
    acc = apidata.account(api, cust)
    for target, status in [
        (link(foreign), 422),
        ({"id": NIL}, 422),
        ({**link(fa), "@referredType": "BillingAccount"}, 422),
        ({**link(fa), "unknown": 1}, 400),
    ]:
        assert api.patch(acc["href"], {"financialAccount": target}).status_code == status
    assert "financialAccount" not in api.get(acc["href"]).json()
    assert api.patch(acc["href"], {"financialAccount": link(fa)}).status_code == 200
    assert api.patch(fa["href"], {"state": "Closed"}).json()["state"] == "Closed"
    # an unchanged link survives the close; a new one to a Closed account does not
    assert api.patch(acc["href"], {"financialAccount": link(fa), "name": "x"}).status_code == 200
    second = apidata.account(api, cust, name="second")
    response = api.patch(second["href"], {"financialAccount": link(fa)})
    assert response.status_code == 422 and "Closed" in response.json()["message"]
    assert (
        api.post(ACCOUNT, apidata.account_body(cust, financialAccount=link(fa))).status_code == 422
    )


def test_owner_must_be_one_existing_party_with_role_owner(api):
    person = apidata.individual(api)
    cases = [
        ([owner(person, role="customer")], 422),
        ([{k: v for k, v in owner(person).items() if k != "role"}], 422),
        ([owner({**person, "id": NIL})], 422),
        ([owner({**person, "@type": "Organization"})], 422),
        ([owner(person), owner(person)], 422),
        ([], 422),
    ]
    for related, status in cases:
        body = {"name": "x", "relatedParty": related}
        assert api.post(FINANCIAL, body).status_code == status, related
    assert api.post(FINANCIAL, {"name": "x"}).status_code == 422
    assert api.get(FINANCIAL).json() == []
    fa = financial(api, person)
    assert fa["relatedParty"][0]["partyOrPartyRole"]["name"] == "Ivan Ivanov"


@pytest.mark.parametrize(
    "extra",
    [
        {"accountBalance": []},
        {"accountRelationship": []},
        {"contact": []},
        {"paymentStatus": "In Arrears"},
        {"@type": "BillingAccount"},
        {"id": NIL},
    ],
)
def test_read_only_and_unsupported_attributes_on_create(api, extra):
    org = apidata.organization(api)
    body = {"name": "x", "relatedParty": [owner(org)], **extra}
    assert api.post(FINANCIAL, body).status_code == 400
    assert api.get(FINANCIAL).json() == []


def test_patch_and_credit_limit(api):
    org = apidata.organization(api)
    fa = financial(api, org, creditLimit={"unit": "USD", "value": 500})
    assert decimals(api.get(fa["href"]))["creditLimit"] == {"unit": "USD", "value": Decimal(500)}
    patched = api.patch(
        fa["href"],
        {
            "name": "Renamed",
            "description": "d",
            "accountType": "Global",
            "state": "Suspended",
            "creditLimit": {"unit": "EUR", "value": 10.5},
        },
    ).json()
    assert (patched["name"], patched["accountType"], patched["state"]) == (
        "Renamed",
        "Global",
        "Suspended",
    )
    assert patched["creditLimit"] == {"unit": "EUR", "value": 10.5}
    cleared = api.patch(fa["href"], {"description": None, "creditLimit": None}).json()
    assert "description" not in cleared and "creditLimit" not in cleared
    for body, status in [
        ({"name": None}, 422),
        ({"name": ""}, 422),
        ({"state": None}, 422),
        ({"state": "Open"}, 422),
        ({"accountType": ""}, 422),
        ({"creditLimit": {"unit": "RUB", "value": -1}}, 422),
        ({"creditLimit": {"unit": "RUB", "value": 0.001}}, 422),
        ({"relatedParty": [owner(org)]}, 400),
        ({"accountBalance": []}, 400),
        ({"accountRelationship": []}, 400),
        ({"lastUpdate": "2026-01-01T00:00:00Z"}, 400),
        ({"@type": "PartyAccount"}, 400),
        ({"taxExemption": []}, 400),
    ]:
        assert api.patch(fa["href"], body).status_code == status, body
    assert api.get(fa["href"]).json() == cleared
    assert (
        api.post(
            FINANCIAL,
            {
                "name": "x",
                "relatedParty": [owner(org)],
                "creditLimit": {"unit": "RUB", "value": -1},
            },
        ).status_code
        == 422
    )


def test_list_filters_fields_and_not_found(api):
    org, person = apidata.organization(api), apidata.individual(api)
    a = financial(api, org, accountType="Business")
    financial(api, person, name="Personal", state="Suspended")
    assert [f["id"] for f in api.get(f"{FINANCIAL}?relatedParty.id={org['id']}").json()] == [
        a["id"]
    ]
    assert [f["name"] for f in api.get(f"{FINANCIAL}?state=Suspended").json()] == ["Personal"]
    assert len(api.get(f"{FINANCIAL}?accountType=Business").json()) == 1
    assert len(api.get(f"{FINANCIAL}?name=Personal").json()) == 1
    response = api.get(f"{FINANCIAL}?fields=name&limit=1")
    assert response.headers["X-Total-Count"] == "2"
    assert set(response.json()[0]) == {"id", "href", "@type", "name"}
    response = api.post(f"{FINANCIAL}?fields=state", {"name": "x", "relatedParty": [owner(org)]})
    assert set(response.json()) == {"id", "href", "@type", "state"}
    assert api.get(f"{FINANCIAL}?balance=1").status_code == 400
    assert api.get(f"{FINANCIAL}?fields=unknown").status_code == 400
    assert api.get(f"{FINANCIAL}/{NIL}").status_code == 404
    assert api.get(f"{FINANCIAL}/invalid").status_code == 404
    assert api.patch(f"{FINANCIAL}/{NIL}", {"name": "x"}).status_code == 404
    assert api.client.put(a["href"], json={}).status_code == 405  # DELETE: test_api_deletes
    assert api.get(f"{PARTY}/organization/{org['id']}").status_code == 200


def test_the_database_refuses_a_link_across_owners(api, db_conn):
    org, other = apidata.organization(api), apidata.organization(api, "Other LLC")
    foreign = financial(api, other)
    acc = apidata.account(api, apidata.customer(api, org))
    with pytest.raises(errors.CheckViolation, match="not owned by the party"):
        db_conn.execute(
            "UPDATE billing_account SET financial_account_id = %s WHERE id = %s",
            (foreign["id"], acc["id"]),
        )
