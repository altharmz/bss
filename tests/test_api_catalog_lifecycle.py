"""Offerings, specifications and prices share one lifecycle (doc-findings #7): through
PATCH, lifecycleStatus only moves In Design -> Active -> Retired, Retired is final, and
any other transition is a 409 with the reason that changes nothing in the patch.
Repeating the current status is not a transition. (POST may create any of them in any
status.)"""

import pytest

from tests.apidata import CATALOG, START

STATUSES = ("In Design", "Active", "Retired")
ALLOWED = {("In Design", "Active"), ("Active", "Retired")}


def created(response) -> dict:
    assert response.status_code == 201, response.text
    return response.json()


def specification(api, status: str) -> dict:
    return created(
        api.post(
            f"{CATALOG}/productSpecification",
            {"@type": "ProductSpecification", "name": "Mobile", "lifecycleStatus": status},
        )
    )


def offering(api, status: str) -> dict:
    spec = specification(api, "Active")
    return created(
        api.post(
            f"{CATALOG}/productOffering",
            {
                "@type": "ProductOffering",
                "name": "Mobile S",
                "lifecycleStatus": status,
                "validFor": {"startDateTime": START},
                "productSpecification": {"id": spec["id"], "@type": "ProductSpecificationRef"},
            },
        )
    )


def price(api, status: str) -> dict:
    return created(
        api.post(
            f"{CATALOG}/productOfferingPrice",
            {
                "@type": "ProductOfferingPrice",
                "name": "Monthly",
                "lifecycleStatus": status,
                "priceType": "recurring",
                "recurringChargePeriodType": "monthly",
                "price": {"unit": "USD", "value": 300},
                "validFor": {"startDateTime": START},
            },
        )
    )


ENTITIES = {"offering": offering, "specification": specification, "price": price}


@pytest.mark.parametrize("entity", ENTITIES)
@pytest.mark.parametrize("current", STATUSES)
@pytest.mark.parametrize("wanted", STATUSES)
def test_every_transition(api, entity, current, wanted):
    made = ENTITIES[entity](api, current)
    response = api.patch(made["href"], {"lifecycleStatus": wanted})
    if wanted == current or (current, wanted) in ALLOWED:
        assert response.status_code == 200, response.text
        assert response.json()["lifecycleStatus"] == wanted
        return
    assert response.status_code == 409, response.text
    message = response.json()["message"]
    if current == "Retired":
        assert message == f"lifecycleStatus: Retired is final; the {entity} cannot change status"
    else:
        assert message == (
            f"lifecycleStatus cannot go from {current} to {wanted}:"
            " only In Design -> Active -> Retired"
        )
    assert api.get(made["href"]).json()["lifecycleStatus"] == current


@pytest.mark.parametrize("entity", ENTITIES)
def test_a_refused_transition_changes_nothing_else_in_the_patch(api, entity):
    made = ENTITIES[entity](api, "Retired")
    response = api.patch(made["href"], {"lifecycleStatus": "In Design", "name": "Reopened"})
    assert response.status_code == 409
    assert api.get(made["href"]).json()["name"] == made["name"]


@pytest.mark.parametrize("entity", ENTITIES)
def test_the_whole_way_forward(api, entity):
    made = ENTITIES[entity](api, "In Design")
    for status in ("Active", "Retired"):
        assert api.patch(made["href"], {"lifecycleStatus": status}).status_code == 200
    assert api.patch(made["href"], {"lifecycleStatus": "Active"}).status_code == 409
