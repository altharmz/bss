"""An offering's lifecycle only moves forward: In Design -> Active -> Retired, and Retired
is final. Any other transition through PATCH is a 409 with the reason; repeating the
current status is not a transition. (POST may create an offering in any status.)"""

import pytest

from tests.apidata import CATALOG, START

STATUSES = ("In Design", "Active", "Retired")
ALLOWED = {("In Design", "Active"), ("Active", "Retired")}


@pytest.fixture
def make_offering(api):
    spec = api.post(
        f"{CATALOG}/productSpecification",
        {"@type": "ProductSpecification", "name": "Mobile", "lifecycleStatus": "Active"},
    ).json()

    def make(status: str) -> dict:
        response = api.post(
            f"{CATALOG}/productOffering",
            {
                "@type": "ProductOffering",
                "name": "Mobile S",
                "lifecycleStatus": status,
                "validFor": {"startDateTime": START},
                "productSpecification": {"id": spec["id"], "@type": "ProductSpecificationRef"},
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    return make


@pytest.mark.parametrize("current", STATUSES)
@pytest.mark.parametrize("wanted", STATUSES)
def test_every_transition(api, make_offering, current, wanted):
    offering = make_offering(current)
    response = api.patch(offering["href"], {"lifecycleStatus": wanted})
    if wanted == current or (current, wanted) in ALLOWED:
        assert response.status_code == 200, response.text
        assert response.json()["lifecycleStatus"] == wanted
        return
    assert response.status_code == 409, response.text
    message = response.json()["message"]
    if current == "Retired":
        assert message == "lifecycleStatus: Retired is final; the offering cannot change status"
    else:
        assert message == (
            f"lifecycleStatus cannot go from {current} to {wanted}:"
            " only In Design -> Active -> Retired"
        )
    # nothing changed
    assert api.get(offering["href"]).json()["lifecycleStatus"] == current


def test_a_refused_transition_changes_nothing_else_in_the_patch(api, make_offering):
    offering = make_offering("Retired")
    response = api.patch(offering["href"], {"lifecycleStatus": "Active", "name": "Reopened"})
    assert response.status_code == 409
    assert api.get(offering["href"]).json()["name"] == "Mobile S"


def test_the_whole_way_forward(api, make_offering):
    offering = make_offering("In Design")
    for status in ("Active", "Retired"):
        assert api.patch(offering["href"], {"lifecycleStatus": status}).status_code == 200
    assert api.patch(offering["href"], {"lifecycleStatus": "In Design"}).status_code == 409
