"""TMF666 billing policy discovery, checked against the vendored contract."""

import pytest

from tests import apidata

BASE = "/tmf-api/accountManagement/v5/billingCycleSpecification"


def test_monthly_policy_is_discoverable_and_accounts_link_to_it(api):
    response = api.get(BASE)
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "1"
    assert response.headers["X-Result-Count"] == "1"
    [policy] = response.json()
    assert policy["id"] == "monthly"
    assert policy["billingPeriod"] == policy["frequency"] == "monthly"
    assert policy["billingDateShift"] == 0
    assert api.get(policy["href"]).json() == policy

    for day in (1, 15, 28):
        body = apidata.account_body(apidata.customer(api))
        body["billCycleDay"] = day
        account = api.post(apidata.ACCOUNT, body).json()
        reference = account["billStructure"]["cycleSpecification"]
        assert reference["@type"] == "BillingCycleSpecificationRef"
        assert reference["@referredType"] == "BillingCycleSpecification"
        assert api.get(reference["href"]).json() == policy
        assert account["billCycleDay"] == day
        assert api.get(account["href"]).json()["billStructure"] == account["billStructure"]


def test_cycle_policy_paging_filtering_and_projection(api):
    page = api.get(f"{BASE}?offset=1&limit=1")
    assert page.json() == []
    assert page.headers["X-Total-Count"] == "1"
    assert page.headers["X-Result-Count"] == "0"
    missing = api.get(f"{BASE}?frequency=weekly")
    assert missing.json() == []
    assert missing.headers["X-Total-Count"] == "0"
    projected = api.get(f"{BASE}?frequency=monthly&billingPeriod=monthly&fields=frequency")
    [policy] = projected.json()
    assert set(policy) == {"id", "href", "@type", "frequency"}
    assert api.get(f"{BASE}/monthly?fields=frequency").json() == policy
    assert len(api.get(f"{BASE}?name=Monthly%20billing").json()) == 1
    assert api.get(f"{BASE}?name=Other").json() == []


@pytest.mark.parametrize(
    "query",
    [
        "frequency=monthly,weekly",
        "frequency=monthly&frequency=weekly",
        "unknown=x",
        "fields=unknown",
        "fields=name&fields=frequency",
        "limit=0",
        "limit=101",
        "offset=-1",
        "offset=0&offset=1",
    ],
)
def test_cycle_policy_rejects_invalid_queries(api, query):
    assert api.get(f"{BASE}?{query}").status_code == 400


def test_cycle_policy_missing_ids_and_retrieve_queries(api):
    assert api.get(f"{BASE}/unknown").status_code == 404
    assert api.get(f"{BASE}/monthly?frequency=monthly").status_code == 400
    assert api.get(f"{BASE}/monthly?fields=unknown").status_code == 400


def test_cycle_policy_and_account_reference_are_read_only(api):
    assert api.post(BASE, {"name": "Weekly"}).status_code == 405
    assert api.patch(f"{BASE}/monthly", {"frequency": "weekly"}).status_code == 405
    assert api.request("DELETE", f"{BASE}/monthly").status_code == 405
    body = apidata.account_body(apidata.customer(api))
    account = api.post(apidata.ACCOUNT, body).json()
    structure = {"@type": "BillStructure", "cycleSpecification": {"id": "weekly"}}
    assert api.patch(account["href"], {"billStructure": structure}).status_code == 400
    body["billStructure"] = structure
    assert api.post(apidata.ACCOUNT, body).status_code == 400
    assert api.get(account["href"]).json()["billStructure"] == account["billStructure"]
