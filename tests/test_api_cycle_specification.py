"""TMF666 billing policy discovery, checked against the vendored contract."""

import pytest

from tests import apidata
from tests.apiclient import total

BASE = "/tmf-api/accountManagement/v5/billingCycleSpecification"


def test_monthly_policy_is_discoverable_and_accounts_link_to_it(api):
    response = api.get(BASE)
    assert response.status_code == 200
    assert total(response, api) == "1"
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
    assert total(page, api) == "1"
    assert page.headers["X-Result-Count"] == "0"
    missing = api.get(f"{BASE}?frequency=weekly")
    assert missing.json() == []
    assert total(missing, api) == "0"
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


def test_the_built_in_specification_is_fixed(api):
    assert api.patch(f"{BASE}/monthly", {"name": "Other"}).status_code == 409
    assert api.request("DELETE", f"{BASE}/monthly").status_code == 409
    assert api.get(f"{BASE}/monthly").json()["name"] == "Monthly billing"


def test_a_stored_specification_names_the_same_monthly_policy(api):
    response = api.post(BASE, {"@type": "BillingCycleSpecification", "name": "Business"})
    assert response.status_code == 201, response.text
    spec = response.json()
    assert response.headers["location"] == spec["href"]
    assert (spec["billingPeriod"], spec["frequency"], spec["billingDateShift"]) == (
        "monthly",
        "monthly",
        0,
    )
    assert api.get(spec["href"]).json() == spec
    assert [s["id"] for s in api.get(BASE).json()] == ["monthly", spec["id"]]
    assert api.get(f"{BASE}?name=Business").json() == [spec]
    patched = api.patch(spec["href"], {"description": "For companies"}).json()
    assert patched["description"] == "For companies"
    assert api.patch(spec["href"], {"name": None}).status_code == 422
    assert api.get(f"{BASE}/{apidata.NIL}").status_code == 404
    assert api.patch(f"{BASE}/{apidata.NIL}", {"name": "x"}).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Weekly", "billingPeriod": "weekly"},
        {"name": "Weekly", "frequency": "weekly"},
        {"name": "Shifted", "billingDateShift": 5},
        {"billingPeriod": "monthly"},
    ],
)
def test_values_billing_cannot_honour_are_refused(api, body):
    assert api.post(BASE, body).status_code == 422
    assert len(api.get(BASE).json()) == 1


def test_an_account_names_a_specification_and_it_stays_while_named(api):
    spec = api.post(BASE, {"name": "Business"}).json()
    body = apidata.account_body(apidata.customer(api))
    body["billStructure"] = {"cycleSpecification": {"id": spec["id"]}}
    account = api.post(apidata.ACCOUNT, body).json()
    assert account["billStructure"]["cycleSpecification"]["id"] == spec["id"]
    delete = api.request("DELETE", spec["href"])
    assert delete.status_code == 409 and "1 billingAccount" in delete.json()["message"]
    back = {"billStructure": {"cycleSpecification": {"id": "monthly"}}}
    assert (
        api.patch(account["href"], back).json()["billStructure"]["cycleSpecification"]["id"]
        == "monthly"
    )
    assert api.request("DELETE", spec["href"]).status_code == 204
    assert api.get(spec["href"]).status_code == 404
    unknown = {"billStructure": {"cycleSpecification": {"id": "weekly"}}}
    assert api.patch(account["href"], unknown).status_code == 422
    nil = {"billStructure": {"cycleSpecification": {"id": apidata.NIL}}}
    assert api.patch(account["href"], nil).status_code == 422
