"""TMF666 partyAccount and settlementAccount: one shape on two paths, owned by a party,
optionally contained in that party's financial account."""

import pytest
from psycopg import errors

from tests import apidata
from tests.apiclient import total
from tests.apidata import NIL
from tests.builders.tmf.api_financial_account import FINANCIAL, financial, link, owner
from tests.builders.tmf.api_party_account import BASE, create

KINDS = [("partyAccount", "PartyAccount"), ("settlementAccount", "SettlementAccount")]


@pytest.mark.parametrize("resource,typ", KINDS)
def test_round_trip(api, resource, typ):
    org = apidata.organization(api)
    acc = create(
        api,
        resource,
        org,
        **{"@type": typ},
        description="Supplier",
        accountType="Business",
        creditLimit={"unit": "EUR", "value": 1000},
    )
    assert acc == {
        "id": acc["id"],
        "href": f"{BASE}/{resource}/{acc['id']}",
        "name": "Partner payouts",
        "description": "Supplier",
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
        "creditLimit": {"unit": "EUR", "value": 1000},
        "accountBalance": [],  # no ledger
        "lastUpdate": acc["lastUpdate"],
        "@type": typ,
    }
    assert api.get(acc["href"]).json() == acc
    assert [a["id"] for a in api.get(f"{BASE}/{resource}").json()] == [acc["id"]]


def test_the_two_resources_are_separate(api):
    org = apidata.organization(api)
    party = create(api, "partyAccount", org)
    settlement = create(api, "settlementAccount", org)
    assert [a["id"] for a in api.get(f"{BASE}/partyAccount").json()] == [party["id"]]
    assert [a["id"] for a in api.get(f"{BASE}/settlementAccount").json()] == [settlement["id"]]
    assert api.get(f"{BASE}/partyAccount/{settlement['id']}").status_code == 404
    assert api.get(f"{BASE}/settlementAccount/{party['id']}").status_code == 404
    assert api.patch(f"{BASE}/partyAccount/{settlement['id']}", {"name": "x"}).status_code == 404
    # @type names the resource of the path: no polymorphic create or patch
    body = {"name": "x", "relatedParty": [owner(org)], "@type": "SettlementAccount"}
    assert api.post(f"{BASE}/partyAccount", body).status_code == 400
    assert api.patch(party["href"], {"@type": "SettlementAccount"}).status_code == 400
    assert api.get(f"{BASE}/billingAccount").json() == []


@pytest.mark.parametrize("resource,typ", KINDS)
def test_financial_account_link_and_relationships(api, resource, typ):
    org = apidata.organization(api)
    fa = financial(api, org)
    billing = apidata.account(api, apidata.customer(api, org), financialAccount=link(fa))
    acc = create(api, resource, org, financialAccount=link(fa))
    assert acc["financialAccount"] == {
        "id": fa["id"],
        "href": fa["href"],
        "name": fa["name"],
        "@type": "FinancialAccountRef",
        "@referredType": "FinancialAccount",
    }
    body = api.get(fa["href"]).json()
    assert [
        (r["account"]["id"], r["account"]["@referredType"], r["account"]["href"])
        for r in body["accountRelationship"]
    ] == [
        (billing["id"], "BillingAccount", billing["href"]),
        (acc["id"], typ, acc["href"]),
    ]
    # no ledger: the balance is the billing account's alone
    assert [b["amount"]["unit"] for b in body["accountBalance"]] == ["USD"]
    assert [
        a["id"] for a in api.get(f"{BASE}/{resource}?financialAccount.id={fa['id']}").json()
    ] == [acc["id"]]
    unlinked = api.patch(acc["href"], {"financialAccount": None}).json()
    assert "financialAccount" not in unlinked
    assert len(api.get(fa["href"]).json()["accountRelationship"]) == 1
    relinked = api.patch(acc["href"], {"financialAccount": link(fa)}).json()
    assert relinked["financialAccount"]["id"] == fa["id"]


@pytest.mark.parametrize("resource,typ", KINDS)
def test_link_needs_the_same_owner_and_an_open_financial_account(api, resource, typ):
    org, other = apidata.organization(api), apidata.organization(api, "Other LLC")
    fa, foreign = financial(api, org), financial(api, other)
    body = {"name": "x", "relatedParty": [owner(org)], "financialAccount": link(foreign)}
    response = api.post(f"{BASE}/{resource}", body)
    assert response.status_code == 422 and "same party" in response.json()["message"]
    assert api.get(f"{BASE}/{resource}").json() == []
    acc = create(api, resource, org)
    for target, status in [
        (link(foreign), 422),
        ({"id": NIL}, 422),
        ({**link(fa), "@referredType": "PartyAccount"}, 422),
    ]:
        assert api.patch(acc["href"], {"financialAccount": target}).status_code == status
    assert api.patch(acc["href"], {"financialAccount": link(fa)}).status_code == 200
    assert api.patch(fa["href"], {"state": "Closed"}).status_code == 200
    assert api.patch(acc["href"], {"financialAccount": link(fa), "name": "y"}).status_code == 200
    second = create(api, resource, org, name="second")
    assert api.patch(second["href"], {"financialAccount": link(fa)}).status_code == 422


@pytest.mark.parametrize("resource,typ", KINDS)
def test_validation(api, resource, typ):
    person = apidata.individual(api)
    url = f"{BASE}/{resource}"
    for related, status in [
        ([owner(person, role="bill receiver")], 422),
        ([owner({**person, "id": NIL})], 422),
        ([owner({**person, "@type": "Organization"})], 422),
        ([owner(person), owner(person)], 422),
    ]:
        assert api.post(url, {"name": "x", "relatedParty": related}).status_code == status
    for extra, status in [
        # the server's, and these accounts have none
        ({"accountBalance": [{"balanceType": "currentBalance"}]}, 422),
        ({"billStructure": {"@type": "BillStructure", "dueDate": "x"}}, 400),
        ({"paymentStatus": 5}, 422),
        ({"defaultPaymentMethod": {"@type": "PaymentMethodRef"}}, 422),  # id required
        # an unknown attribute and the required ones missing
        ({"accountRelationship": [{"@type": "AccountRelationship", "x": 1}]}, 422),
        ({"name": ""}, 422),
        ({"state": "Open"}, 422),
        ({"creditLimit": {"unit": "USD", "value": -5}}, 422),
    ]:
        body = {"name": "x", "relatedParty": [owner(person)], **extra}
        assert api.post(url, body).status_code == status, extra
    assert api.get(url).json() == []
    acc = create(api, resource, person, description="d", creditLimit={"unit": "USD", "value": 5})
    patched = api.patch(
        acc["href"], {"state": "Suspended", "accountType": "Wholesale", "description": None}
    ).json()
    assert (patched["state"], patched["accountType"]) == ("Suspended", "Wholesale")
    assert "description" not in patched
    assert "creditLimit" not in api.patch(acc["href"], {"creditLimit": None}).json()
    for body, status in [
        ({"name": None}, 422),
        ({"state": None}, 422),
        ({"relatedParty": []}, 422),
        ({"lastUpdate": "2026-01-01T00:00:00Z"}, 422),
        ({"accountBalance": [{"balanceType": "currentBalance"}]}, 422),
        ({"contact": "x"}, 422),
    ]:
        assert api.patch(acc["href"], body).status_code == status, body
    assert total(api.get(f"{url}?relatedParty.id={person['id']}"), api) == "1"
    assert api.get(f"{url}?state=Active").json() == []
    assert len(api.get(f"{url}?accountType=Wholesale&name=Partner%20payouts").json()) == 1
    assert api.get(f"{url}?accountType=Wholesale&name=x").json() == []
    response = api.get(f"{url}?fields=financialAccount,state")
    assert set(response.json()[0]) == {"id", "href", "@type", "state"}
    assert api.get(f"{url}?balance=1").status_code == 400
    assert api.get(f"{url}/{NIL}").status_code == 404
    assert api.get(f"{url}/invalid").status_code == 404
    assert api.client.put(acc["href"], json={}).status_code == 405  # DELETE: test_api_deletes


def test_the_database_refuses_a_link_across_owners(api, db_conn):
    org, other = apidata.organization(api), apidata.organization(api, "Other LLC")
    foreign = financial(api, other)
    acc = create(api, "settlementAccount", org)
    with pytest.raises(errors.ForeignKeyViolation, match="same_owner"):
        db_conn.execute(
            "UPDATE party_account SET financial_account_id = %s WHERE id = %s",
            (foreign["id"], acc["id"]),
        )


def test_financial_accounts_list_is_unaffected(api):
    org = apidata.organization(api)
    create(api, "partyAccount", org)
    assert api.get(FINANCIAL).json() == []
