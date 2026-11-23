"""GET /internal/v1/party and /internal/v1/partyCustomer (#314): the Parties screen's
lists and the account owner picker, paged on the server with no count, searched by the
start of a name or a short id, with per-page figures."""

import pytest

from tests import apidata
from tests.builders.api_financial_account import financial

pytestmark = pytest.mark.usefixtures("match_path")

PARTIES = "/internal/v1/party"
CUSTOMERS = "/internal/v1/partyCustomer"


def names(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [row["name"] for row in response.json()]


def test_party_name_prefix_any_case_given_full_family_or_organization(api):
    apidata.individual(api, "Ivan", "Petrov")
    apidata.individual(api, "Olga", "Ivanova")
    apidata.individual(api, "Anna", "Smirnova")
    apidata.organization(api, "Ivanhoe Ltd")
    assert names(api.get(f"{PARTIES}?q=ivan")) == ["Ivan Petrov", "Olga Ivanova", "Ivanhoe Ltd"]
    assert names(api.get(f"{PARTIES}?q=IVAN%20%20pe")) == ["Ivan Petrov"]  # spaces collapse
    assert names(api.get(f"{PARTIES}?q=ivan&kind=Individual")) == ["Ivan Petrov", "Olga Ivanova"]
    assert names(api.get(f"{PARTIES}?q=ova")) == []  # a prefix, not a part of the name
    assert names(api.get(f"{PARTIES}?q=_")) == []  # LIKE wildcards are matched literally


def test_party_short_and_full_id(api):
    party = apidata.individual(api, "Ivan", "Petrov")
    apidata.individual(api, "Olga", "Ivanova")
    assert party["id"] in [r["id"] for r in api.get(f"{PARTIES}?q={party['id'][:6]}").json()]
    assert [r["id"] for r in api.get(f"{PARTIES}?q={party['id']}").json()] == [party["id"]]


def test_party_rows_carry_their_customers_accounts_and_ownership(api):
    org = apidata.organization(api, "Romashka LLC")
    cust = apidata.customer(api, org, name="Romashka")
    first = apidata.account(api, cust, name="HQ")
    apidata.account(api, cust, name="Branch")
    lone = apidata.individual(api, "Anna", "Smirnova")
    financial(api, lone)
    rows = {r["name"]: r for r in api.get(f"{PARTIES}?limit=10").json()}
    romashka, anna = rows["Romashka LLC"], rows["Anna Smirnova"]
    assert romashka["kind"] == "Organization" and romashka["ownsAccounts"] is False
    assert romashka["customers"] == [{"id": cust["id"], "name": "Romashka"}]
    assert [a["name"] for a in romashka["billingAccounts"]] == ["HQ", "Branch"]
    assert romashka["billingAccounts"][0] == {
        "id": first["id"],
        "name": "HQ",
        "customerId": cust["id"],
    }
    assert anna["customers"] == [] and anna["ownsAccounts"] is True


def test_party_filters_paging_and_bad_parameters(api):
    for given in ("A", "B", "C"):
        apidata.individual(api, given, "Kuznetsov")
    page = api.get(f"{PARTIES}?limit=2")
    assert len(page.json()) == 2 and 'rel="next"' in page.headers["Link"]
    assert "X-Total-Count" not in page.headers
    assert "Link" not in api.get(f"{PARTIES}?offset=2&limit=2").headers
    assert names(api.get(f"{PARTIES}?status=validated")) == []
    assert api.get(f"{PARTIES}?status=Bogus").status_code == 400
    assert api.get(f"{PARTIES}?kind=Customer").status_code == 400
    assert api.get(f"{PARTIES}?q=a&q=b").status_code == 400
    assert api.get(f"{PARTIES}?name=a").status_code == 400


def test_customer_search_with_party_and_accounts(api):
    ivan = apidata.individual(api, "Ivan", "Petrov")
    cust = apidata.customer(api, ivan, name="Ivan Petrov")
    acc = apidata.account(api, cust, name="Main")
    apidata.customer(api, apidata.organization(api, "Romashka LLC"), name="Romashka")
    rows = api.get(f"{CUSTOMERS}?q=iv").json()
    assert rows == [
        {
            "id": cust["id"],
            "name": "Ivan Petrov",
            "status": cust["status"],
            "party": {"id": ivan["id"], "kind": "Individual", "name": "Ivan Petrov"},
            "billingAccounts": [{"id": acc["id"], "name": "Main"}],
        }
    ]
    assert names(api.get(f"{CUSTOMERS}?q={cust['id'][:8]}")) == ["Ivan Petrov"]
    assert names(api.get(f"{CUSTOMERS}?status=Inactive")) == []
    assert api.get(f"{CUSTOMERS}?status=Bogus").status_code == 400


def test_the_party_prefix_searches_use_indexes(db_conn):
    db_conn.execute("SET enable_seqscan = off")
    for query in (
        "SELECT id FROM party WHERE party_type = 'Individual'"
        " AND lower(coalesce(given_name, '') || ' ' || family_name) LIKE 'ivan%'",
        "SELECT id FROM party WHERE party_type = 'Individual' AND lower(family_name) LIKE 'iv%'",
        "SELECT id FROM party WHERE party_type = 'Organization'"
        " AND lower(organization_name) LIKE 'iv%'",
    ):
        plan = "\n".join(r[0] for r in db_conn.execute("EXPLAIN " + query))
        assert "Seq Scan" not in plan, plan
